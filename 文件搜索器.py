# -*- coding: utf-8 -*-
"""
文件搜索器
------------------------------------------------
功能：
  * 多驱动器 / 多文件夹同时扫描
  * 关键词匹配（包含 / 通配符 / 正则）
  * 后台线程搜索，界面不卡顿，可随时停止
  * 右键菜单：打开、打开所在目录、复制路径、重命名、删除
  * 删除优先送入回收站（需安装 send2trash，未安装则永久删除）

依赖：仅标准库（tkinter）；可选 pip install Send2Trash
"""

import fnmatch
import os
import queue
import re
import shutil
import string
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

# ---------------------------------------------------------------- 可选依赖
try:
    from send2trash import send2trash
    HAS_SEND2TRASH = True
except ImportError:
    HAS_SEND2TRASH = False

IS_WINDOWS = sys.platform.startswith('win')
IS_MAC = sys.platform == 'darwin'

# 搜索时默认跳过的系统目录名
SKIP_DIRS = {
    '$Recycle.Bin', '$RECYCLE.BIN', 'System Volume Information',
    'Recovery', 'Config.Msi', 'MSOCache',
}


# ---------------------------------------------------------------- 工具函数
def get_drives():
    """返回可扫描的根路径列表"""
    drives = []
    if IS_WINDOWS:
        for letter in string.ascii_uppercase:
            p = '{}:\\'.format(letter)
            if os.path.exists(p):
                drives.append(p)
    else:
        drives.append('/')
        if IS_MAC and os.path.isdir('/Volumes'):
            try:
                for name in sorted(os.listdir('/Volumes')):
                    p = os.path.join('/Volumes', name)
                    if os.path.isdir(p):
                        drives.append(p)
            except OSError:
                pass
    return drives


def human_size(size):
    """字节数转可读字符串"""
    if size < 1024:
        return '{} B'.format(size)
    for unit in ('KB', 'MB', 'GB', 'TB'):
        size /= 1024.0
        if size < 1024:
            return '{:.1f} {}'.format(size, unit)
    return '{:.1f} PB'.format(size)


def build_matcher(keyword, mode, case_sensitive):
    """根据匹配模式返回一个 name -> bool 的函数"""
    if mode == '正则':
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(keyword, flags)
        return lambda name: regex.search(name) is not None

    if mode == '通配符':
        if case_sensitive:
            return lambda name: fnmatch.fnmatchcase(name, keyword)
        kw = keyword.lower()
        return lambda name: fnmatch.fnmatch(name.lower(), kw)

    # 包含
    if case_sensitive:
        return lambda name: keyword in name
    kw = keyword.lower()
    return lambda name: kw in name.lower()


def open_path(path):
    """用系统默认程序打开文件/文件夹"""
    if IS_WINDOWS:
        os.startfile(path)                       # noqa
    elif IS_MAC:
        subprocess.Popen(['open', path])
    else:
        subprocess.Popen(['xdg-open', path])


def reveal_path(path):
    """在文件管理器中定位并选中该文件"""
    path = os.path.normpath(path)
    try:
        if IS_WINDOWS:
            subprocess.Popen('explorer /select,"{}"'.format(path))
        elif IS_MAC:
            subprocess.Popen(['open', '-R', path])
        else:
            subprocess.Popen(['xdg-open', os.path.dirname(path) or '/'])
    except Exception:
        open_path(os.path.dirname(path) or '/')


def remove_forever(path):
    """彻底删除（不经过回收站）"""
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


# ---------------------------------------------------------------- 主程序
class FileSearcherApp:
    MAX_RESULTS = 50000          # 结果上限，防止内存爆掉
    POLL_INTERVAL = 80           # 主线程轮询队列间隔(ms)
    BATCH_PER_POLL = 300         # 每次轮询最多处理的结果条数

    def __init__(self, root):
        self.root = root
        self.root.title('文件搜索器')
        self.root.geometry('1020x660')
        self.root.minsize(780, 480)

        self._running = True
        self.result_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.search_thread = None

        self.path_map = {}        # iid -> 完整路径
        self.iid_counter = 0

        self.scope_vars = {}      # 路径 -> BooleanVar
        self.custom_widgets = []  # [(checkbutton, path), ...]

        self._build_ui()
        self._poll_queue()

    # ------------------------------------------------------------ 界面
    def _build_ui(self):
        try:
            if IS_WINDOWS:
                ttk.Style().theme_use('vista')
        except tk.TclError:
            pass

        # ---- 扫描范围 ----
        scope_frame = ttk.LabelFrame(self.root, text='扫描范围（可多选）')
        scope_frame.pack(fill='x', padx=8, pady=(8, 4))

        self.scope_container = ttk.Frame(scope_frame)
        self.scope_container.pack(fill='x', padx=6, pady=6)
        self._load_drives()

        bar = ttk.Frame(scope_frame)
        bar.pack(fill='x', padx=6, pady=(0, 6))
        ttk.Button(bar, text='全选', width=8,
                   command=lambda: self._set_all(True)).pack(side='left')
        ttk.Button(bar, text='全不选', width=8,
                   command=lambda: self._set_all(False)).pack(side='left', padx=4)
        ttk.Button(bar, text='添加文件夹…',
                   command=self._add_folder).pack(side='left', padx=4)
        ttk.Button(bar, text='清除自定义',
                   command=self._clear_custom).pack(side='left', padx=4)

        # ---- 搜索条件 ----
        cond = ttk.LabelFrame(self.root, text='搜索条件')
        cond.pack(fill='x', padx=8, pady=4)

        row1 = ttk.Frame(cond)
        row1.pack(fill='x', padx=6, pady=6)

        ttk.Label(row1, text='关键词：').pack(side='left')
        self.keyword_var = tk.StringVar()
        self.entry = ttk.Entry(row1, textvariable=self.keyword_var)
        self.entry.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.entry.bind('<Return>', lambda e: self.start_search())

        ttk.Label(row1, text='匹配：').pack(side='left')
        self.mode_var = tk.StringVar(value='包含')
        ttk.Combobox(row1, textvariable=self.mode_var, state='readonly', width=8,
                     values=['包含', '通配符', '正则']).pack(side='left', padx=(0, 8))

        self.case_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row1, text='区分大小写',
                        variable=self.case_var).pack(side='left')

        self.skip_sys_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1, text='跳过系统目录',
                        variable=self.skip_sys_var).pack(side='left', padx=8)

        row2 = ttk.Frame(cond)
        row2.pack(fill='x', padx=6, pady=(0, 6))
        self.search_btn = ttk.Button(row2, text='开始搜索', command=self.start_search)
        self.search_btn.pack(side='left')
        self.stop_btn = ttk.Button(row2, text='停止', state='disabled',
                                   command=self.stop_search)
        self.stop_btn.pack(side='left', padx=6)

        # ---- 结果列表 ----
        result_frame = ttk.Frame(self.root)
        result_frame.pack(fill='both', expand=True, padx=8, pady=4)

        columns = ('name', 'folder', 'size', 'mtime')
        self.tree = ttk.Treeview(result_frame, columns=columns, show='headings',
                                 selectmode='extended')
        self.tree.heading('name', text='文件名')
        self.tree.heading('folder', text='所在目录')
        self.tree.heading('size', text='大小')
        self.tree.heading('mtime', text='修改时间')
        self.tree.column('name', width=250, anchor='w')
        self.tree.column('folder', width=430, anchor='w')
        self.tree.column('size', width=90, anchor='e')
        self.tree.column('mtime', width=140, anchor='center')
        self.tree.tag_configure('odd', background='#f4f6f8')

        vsb = ttk.Scrollbar(result_frame, orient='vertical', command=self.tree.yview)
        hsb = ttk.Scrollbar(result_frame, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)

        # ---- 状态栏 ----
        status = ttk.Frame(self.root)
        status.pack(fill='x', padx=8, pady=(0, 8))
        self.status_var = tk.StringVar(value='就绪')
        ttk.Label(status, textvariable=self.status_var, anchor='w').pack(side='left')
        self.progress = ttk.Progressbar(status, mode='indeterminate', length=170)
        self.progress.pack(side='right')

        # ---- 右键菜单 ----
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label='打开', command=self.open_selected)
        self.menu.add_command(label='打开所在目录', command=self.reveal_selected)
        self.menu.add_separator()
        self.menu.add_command(label='复制完整路径', command=self.copy_path)
        self.menu.add_command(label='重命名…', command=self.rename_selected)
        self.menu.add_separator()
        self.menu.add_command(label='删除', command=self.delete_selected)

        self.tree.bind('<Button-3>', self._on_right_click)      # Windows / Linux
        self.tree.bind('<Button-2>', self._on_right_click)      # macOS
        self.tree.bind('<Double-1>', lambda e: self.open_selected())
        self.tree.bind('<Control-a>', self._select_all)

        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    # ------------------------------------------------------------ 范围管理
    def _load_drives(self):
        for w in self.scope_container.winfo_children():
            w.destroy()
        self.scope_vars.clear()
        self.custom_widgets.clear()

        drives = get_drives() or [os.path.expanduser('~')]
        for i, d in enumerate(drives):
            var = tk.BooleanVar(value=True)
            cb = ttk.Checkbutton(self.scope_container, text=d, variable=var)
            cb.grid(row=i // 6, column=i % 6, sticky='w', padx=4, pady=2)
            self.scope_vars[d] = var

    def _set_all(self, value):
        for v in self.scope_vars.values():
            v.set(value)

    def _add_folder(self):
        folder = filedialog.askdirectory(title='选择要扫描的文件夹', parent=self.root)
        if not folder:
            return
        folder = os.path.normpath(folder)
        if folder in self.scope_vars:
            messagebox.showinfo('提示', '该文件夹已在列表中', parent=self.root)
            return
        var = tk.BooleanVar(value=True)
        n = len(self.scope_vars)
        cb = ttk.Checkbutton(self.scope_container, text=folder, variable=var)
        cb.grid(row=n // 6, column=n % 6, sticky='w', padx=4, pady=2)
        self.scope_vars[folder] = var
        self.custom_widgets.append((cb, folder))

    def _clear_custom(self):
        for cb, path in self.custom_widgets:
            cb.destroy()
            self.scope_vars.pop(path, None)
        self.custom_widgets.clear()

    # ------------------------------------------------------------ 搜索
    def start_search(self):
        if self.search_thread and self.search_thread.is_alive():
            return

        roots = [p for p, v in self.scope_vars.items() if v.get()]
        if not roots:
            messagebox.showwarning('提示', '请至少选择一个扫描范围', parent=self.root)
            return

        keyword = self.keyword_var.get().strip()
        if not keyword:
            messagebox.showwarning('提示', '请输入搜索关键词', parent=self.root)
            return

        try:
            matcher = build_matcher(keyword, self.mode_var.get(), self.case_var.get())
        except re.error as e:
            messagebox.showerror('正则表达式错误', str(e), parent=self.root)
            return

        # 清空上次结果
        self.tree.delete(*self.tree.get_children())
        self.path_map.clear()
        self.iid_counter = 0

        self.stop_event.clear()
        self.search_btn.config(state='disabled')
        self.stop_btn.config(state='normal')
        self.progress.start(12)
        self.status_var.set('正在搜索…')

        self.search_thread = threading.Thread(
            target=self._search_worker,
            args=(roots, matcher, self.skip_sys_var.get()),
            daemon=True)
        self.search_thread.start()

    def stop_search(self):
        if self.search_thread and self.search_thread.is_alive():
            self.stop_event.set()
            self.status_var.set('正在停止…')

    def _search_worker(self, roots, matcher, skip_system):
        """后台线程：深度优先遍历目录树"""
        count = 0
        dirs_scanned = 0
        stack = list(roots)
        seen = set()
        q = self.result_queue
        ev = self.stop_event

        while stack:
            if ev.is_set():
                break

            current = stack.pop()

            # 去重（避免符号链接 / 重叠的根目录导致重复扫描）
            try:
                real = os.path.realpath(current)
            except OSError:
                real = current
            if real in seen:
                continue
            seen.add(real)

            dirs_scanned += 1
            if dirs_scanned % 40 == 0:
                q.put(('progress', dirs_scanned, count))

            try:
                with os.scandir(current) as it:
                    for entry in it:
                        if ev.is_set():
                            break
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if skip_system and entry.name in SKIP_DIRS:
                                    continue
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                if matcher(entry.name):
                                    try:
                                        st = entry.stat()
                                        size, mtime = st.st_size, st.st_mtime
                                    except OSError:
                                        size, mtime = 0, 0
                                    q.put(('hit', entry.path, size, mtime))
                                    count += 1
                                    if count >= self.MAX_RESULTS:
                                        ev.set()
                                        q.put(('limit', self.MAX_RESULTS))
                                        break
                        except OSError:
                            continue
            except (PermissionError, OSError):
                continue

        q.put(('done', dirs_scanned, count, ev.is_set()))

    # ------------------------------------------------------------ 队列轮询
    def _poll_queue(self):
        if not self._running:
            return

        processed = 0
        try:
            while processed < self.BATCH_PER_POLL:
                item = self.result_queue.get_nowait()
                processed += 1
                kind = item[0]

                if kind == 'hit':
                    self._add_result(item[1], item[2], item[3])
                elif kind == 'progress':
                    self.status_var.set(
                        '已扫描 {} 个文件夹，找到 {} 个结果…'.format(item[1], item[2]))
                elif kind == 'limit':
                    messagebox.showinfo(
                        '提示',
                        '结果数量已达上限 {} 条，搜索自动停止。'.format(item[1]),
                        parent=self.root)
                elif kind == 'done':
                    self._on_search_done(item[1], item[2], item[3])
        except queue.Empty:
            pass

        self.root.after(self.POLL_INTERVAL, self._poll_queue)

    def _add_result(self, path, size, mtime):
        self.iid_counter += 1
        iid = str(self.iid_counter)
        name = os.path.basename(path) or path
        folder = os.path.dirname(path)
        tag = 'odd' if self.iid_counter % 2 else ''
        self.tree.insert(
            '', 'end', iid=iid, tags=(tag,),
            values=(name, folder, human_size(size),
                    time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime))))
        self.path_map[iid] = path

    def _on_search_done(self, dirs, count, stopped):
        self.progress.stop()
        self.search_btn.config(state='normal')
        self.stop_btn.config(state='disabled')
        msg = '完成：扫描 {} 个文件夹，找到 {} 个文件'.format(dirs, count)
        if stopped:
            msg += '（已中断）'
        self.status_var.set(msg)

    # ------------------------------------------------------------ 选择与菜单
    def _selected_paths(self):
        return [self.path_map[i] for i in self.tree.selection() if i in self.path_map]

    def _select_all(self, event=None):
        self.tree.selection_set(self.tree.get_children())
        return 'break'

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    # ------------------------------------------------------------ 各操作
    def open_selected(self):
        paths = self._selected_paths()
        if not paths:
            return
        for p in paths:
            try:
                open_path(p)
            except Exception as e:
                messagebox.showerror('打开失败', '{}\n{}'.format(p, e), parent=self.root)

    def reveal_selected(self):
        paths = self._selected_paths()
        if not paths:
            return
        try:
            reveal_path(paths[0])
        except Exception as e:
            messagebox.showerror('打开失败', str(e), parent=self.root)

    def copy_path(self):
        paths = self._selected_paths()
        if not paths:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append('\n'.join(paths))
        self.status_var.set('已复制 {} 条路径到剪贴板'.format(len(paths)))

    def rename_selected(self):
        iids = self.tree.selection()
        if len(iids) != 1:
            messagebox.showinfo('提示', '请选择一个文件进行重命名', parent=self.root)
            return

        iid = iids[0]
        old_path = self.path_map.get(iid)
        if not old_path:
            return

        old_name = os.path.basename(old_path)
        new_name = simpledialog.askstring(
            '重命名', '请输入新名称：', initialvalue=old_name, parent=self.root)
        if not new_name:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        if os.sep in new_name or '/' in new_name or '\\' in new_name:
            messagebox.showerror('错误', '名称中不能包含路径分隔符', parent=self.root)
            return

        new_path = os.path.join(os.path.dirname(old_path), new_name)
        if os.path.exists(new_path):
            messagebox.showerror('错误', '同名文件已存在', parent=self.root)
            return

        try:
            os.rename(old_path, new_path)
        except OSError as e:
            messagebox.showerror('重命名失败', str(e), parent=self.root)
            return

        self.path_map[iid] = new_path
        try:
            st = os.stat(new_path)
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, 0

        self.tree.item(iid, values=(
            new_name, os.path.dirname(new_path), human_size(size),
            time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime))))
        self.status_var.set('已重命名为：{}'.format(new_name))

    def delete_selected(self):
        iids = list(self.tree.selection())
        if not iids:
            return

        paths = [(iid, self.path_map[iid]) for iid in iids if iid in self.path_map]
        if not paths:
            return

        if HAS_SEND2TRASH:
            hint = '这些文件将被移入回收站。'
        else:
            hint = '这些文件将被永久删除，无法恢复！'

        preview = '\n'.join(p for _, p in paths[:5])
        if len(paths) > 5:
            preview += '\n…… 共 {} 项'.format(len(paths))

        if not messagebox.askyesno(
                '确认删除', '确定要删除以下内容吗？\n\n{}\n\n{}'.format(preview, hint),
                parent=self.root):
            return

        errors = []
        ok = 0
        for iid, p in paths:
            try:
                if HAS_SEND2TRASH:
                    send2trash(p)
                else:
                    remove_forever(p)
            except Exception as e:
                errors.append('{}: {}'.format(p, e))
                continue
            self.tree.delete(iid)
            self.path_map.pop(iid, None)
            ok += 1

        if errors:
            messagebox.showerror('部分删除失败', '\n'.join(errors[:10]), parent=self.root)
        self.status_var.set('已删除 {} 项'.format(ok))

    # ------------------------------------------------------------ 关闭
    def _on_close(self):
        self._running = False
        self.stop_event.set()
        self.root.destroy()


def main():
    root = tk.Tk()
    FileSearcherApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()