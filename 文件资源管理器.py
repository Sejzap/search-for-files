# -*- coding: utf-8 -*-
"""
简易文件资源管理器（仅浏览）
仅依赖 Python 标准库 tkinter
"""

import os
import stat
import sys
import string
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

IS_WINDOWS = sys.platform.startswith('win')
IS_MAC = sys.platform == 'darwin'

# 文件类型分组（用于显示不同颜色的图标）
CODE_EXTS = {'.py', '.js', '.ts', '.java', '.c', '.h', '.cpp', '.hpp', '.cs',
             '.go', '.rs', '.rb', '.php', '.html', '.css', '.json', '.xml',
             '.yml', '.yaml', '.sh', '.bat', '.sql', '.kt', '.swift', '.toml'}
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp',
              '.svg', '.tif', '.tiff'}
ARCHIVE_EXTS = {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.iso'}
MEDIA_EXTS = {'.mp3', '.wav', '.flac', '.aac', '.mp4', '.mkv', '.avi',
              '.mov', '.wmv', '.flv', '.webm', '.m4a'}


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------
def format_size(size):
    """把字节数格式化成易读的字符串"""
    if size is None:
        return ''
    if size < 1024:
        return f'{size} B'
    s = float(size)
    for unit in ('KB', 'MB', 'GB', 'TB', 'PB'):
        s /= 1024.0
        if s < 1024:
            return f'{s:.1f} {unit}'
    return f'{s:.1f} EB'


def format_time(ts):
    """时间戳 -> 字符串"""
    try:
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
    except (OSError, ValueError, OverflowError):
        return ''


# ----------------------------------------------------------------------
# 图标（用 PhotoImage 逐像素画，避免依赖外部图片文件）
# ----------------------------------------------------------------------
def make_folder_icon(fill='#FFCE4A', edge='#DFA400'):
    img = tk.PhotoImage(width=16, height=16)
    img.put(edge, to=(0, 1, 8, 4))      # 文件夹小标签
    img.put(fill, to=(1, 2, 7, 4))
    img.put(edge, to=(0, 3, 16, 15))    # 主体边框
    img.put(fill, to=(1, 4, 15, 14))    # 主体填充
    return img


def make_file_icon(fill='#FFFFFF', edge='#8FA3B0'):
    img = tk.PhotoImage(width=16, height=16)
    img.put(edge, to=(2, 1, 13, 15))
    img.put(fill, to=(3, 2, 12, 14))
    return img


def make_drive_icon():
    img = tk.PhotoImage(width=16, height=16)
    img.put('#78909C', to=(1, 3, 15, 13))
    img.put('#CFD8DC', to=(2, 4, 14, 9))
    img.put('#546E7A', to=(2, 10, 14, 12))
    return img


# ----------------------------------------------------------------------
# 主程序
# ----------------------------------------------------------------------
class FileExplorer:

    def __init__(self, root):
        self.root = root
        root.title('文件资源管理器')
        root.geometry('1100x680')
        root.minsize(780, 460)

        # ---------- 状态 ----------
        self.history = []            # 浏览历史
        self.history_index = -1
        self.current_path = os.path.expanduser('~')
        self.entries = []            # 当前目录所有条目
        self.iid_map = {}            # Treeview item id -> 条目 dict
        self.tree_loaded = set()     # 已经加载过子目录的树节点
        self.sort_key = 'name'
        self.sort_reverse = False

        self.show_hidden = tk.BooleanVar(value=False)
        self.filter_var = tk.StringVar()
        self.path_var = tk.StringVar()
        self.status_var = tk.StringVar()

        self._build_icons()
        self._build_style()
        self._build_ui()
        self._build_menu()
        self._bind_events()

        self._populate_roots()
        self.navigate(self.current_path)

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def _build_icons(self):
        self.icons = {
            'folder': make_folder_icon(),
            'file': make_file_icon(),
            'drive': make_drive_icon(),
            'code': make_file_icon('#E3F2FD', '#4A90D9'),
            'image': make_file_icon('#E8F5E9', '#4CAF50'),
            'archive': make_file_icon('#FFF3E0', '#FF9800'),
            'media': make_file_icon('#F3E5F5', '#9C27B0'),
        }

    def _build_style(self):
        if IS_WINDOWS:
            family = 'Microsoft YaHei UI'
        elif IS_MAC:
            family = 'PingFang SC'
        else:
            family = 'DejaVu Sans'
        style = ttk.Style()
        style.configure('Treeview', rowheight=25, font=(family, 10))
        style.configure('Treeview.Heading', font=(family, 10))

    def _build_ui(self):
        # ---------------- 顶部工具栏 ----------------
        toolbar = ttk.Frame(self.root, padding=(6, 6, 6, 4))
        toolbar.pack(side='top', fill='x')

        self.btn_back = ttk.Button(toolbar, text='←', width=3, command=self.go_back)
        self.btn_forward = ttk.Button(toolbar, text='→', width=3, command=self.go_forward)
        self.btn_up = ttk.Button(toolbar, text='↑', width=3, command=self.go_up)
        self.btn_refresh = ttk.Button(toolbar, text='⟳', width=3, command=self.refresh)

        for b in (self.btn_back, self.btn_forward, self.btn_up, self.btn_refresh):
            b.pack(side='left', padx=1)

        ttk.Separator(toolbar, orient='vertical').pack(side='left', fill='y', padx=6)

        self.path_entry = ttk.Entry(toolbar, textvariable=self.path_var)
        self.path_entry.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.path_entry.bind('<Return>', self._on_path_enter)

        # 右侧：隐藏文件开关 + 筛选框
        self.chk_hidden = ttk.Checkbutton(toolbar, text='显示隐藏项',
                                          variable=self.show_hidden,
                                          command=self._on_toggle_hidden)
        self.chk_hidden.pack(side='right', padx=(6, 0))

        ttk.Label(toolbar, text='筛选:').pack(side='right', padx=(10, 2))
        self.search_entry = ttk.Entry(toolbar, textvariable=self.filter_var, width=18)
        self.search_entry.pack(side='right')

        # ---------------- 底部状态栏 ----------------
        status = ttk.Frame(self.root, padding=(10, 3))
        status.pack(side='bottom', fill='x')
        ttk.Separator(self.root, orient='horizontal').pack(side='bottom', fill='x')
        ttk.Label(status, textvariable=self.status_var).pack(side='left')

        # ---------------- 中间主区域 ----------------
        paned = ttk.PanedWindow(self.root, orient='horizontal')
        paned.pack(fill='both', expand=True, padx=6, pady=(0, 6))

        # 左侧目录树
        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        self.tree = ttk.Treeview(left, show='tree', selectmode='browse')
        tree_vsb = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_vsb.set)
        tree_vsb.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)

        # 右侧文件列表
        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        columns = ('size', 'type', 'mtime')
        self.file_list = ttk.Treeview(right, columns=columns,
                                      show='tree headings',
                                      selectmode='extended')

        self.file_list.heading('#0', text='名称', command=lambda: self.sort_by('name'))
        self.file_list.heading('size', text='大小', command=lambda: self.sort_by('size'))
        self.file_list.heading('type', text='类型', command=lambda: self.sort_by('type'))
        self.file_list.heading('mtime', text='修改时间', command=lambda: self.sort_by('mtime'))

        self.file_list.column('#0', width=300, minwidth=140, anchor='w', stretch=True)
        self.file_list.column('size', width=100, minwidth=70, anchor='e', stretch=False)
        self.file_list.column('type', width=120, minwidth=80, anchor='w', stretch=False)
        self.file_list.column('mtime', width=150, minwidth=110, anchor='w', stretch=False)

        list_vsb = ttk.Scrollbar(right, orient='vertical', command=self.file_list.yview)
        list_hsb = ttk.Scrollbar(right, orient='horizontal', command=self.file_list.xview)
        self.file_list.configure(yscrollcommand=list_vsb.set, xscrollcommand=list_hsb.set)

        list_vsb.pack(side='right', fill='y')
        list_hsb.pack(side='bottom', fill='x')
        self.file_list.pack(side='left', fill='both', expand=True)

        self._update_headings()

    def _build_menu(self):
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label='打开', command=self._open_selected)
        self.menu.add_command(label='在系统中定位', command=self._reveal_selected)
        self.menu.add_separator()
        self.menu.add_command(label='复制路径', command=self._copy_path)
        self.menu.add_separator()
        self.menu.add_command(label='属性', command=self._show_props)

    def _bind_events(self):
        self.tree.bind('<<TreeviewOpen>>', self._on_tree_open)
        self.tree.bind('<<TreeviewSelect>>', self._on_tree_select)

        self.file_list.bind('<Double-1>', self._on_double_click)
        self.file_list.bind('<Return>', self._open_selected)
        self.file_list.bind('<<TreeviewSelect>>', self._on_list_select)

        # 右键菜单（macOS 用 Button-2）
        self.file_list.bind('<Button-3>' if not IS_MAC else '<Button-2>', self._show_menu)

        # 过滤输入实时生效
        self.filter_var.trace_add('write', lambda *a: self._render_entries())

        # 快捷键
        self.root.bind('<Alt-Left>', lambda e: self.go_back())
        self.root.bind('<Alt-Right>', lambda e: self.go_forward())
        self.root.bind('<Alt-Up>', lambda e: self.go_up())
        self.root.bind('<F5>', lambda e: self.refresh())
        self.root.bind('<BackSpace>', self._on_backspace)
        self.root.bind('<Control-l>', self._focus_path_entry)

    # ------------------------------------------------------------------
    # 目录树
    # ------------------------------------------------------------------
    def _populate_roots(self):
        """填充根节点（Windows 下是各盘符）"""
        if IS_WINDOWS:
            roots = [f'{c}:\\' for c in string.ascii_uppercase
                     if os.path.exists(f'{c}:\\')]
        else:
            roots = ['/']
            volumes = '/Volumes'
            if IS_MAC and os.path.isdir(volumes):
                try:
                    for name in sorted(os.listdir(volumes)):
                        p = os.path.join(volumes, name)
                        if os.path.isdir(p):
                            roots.append(p)
                except OSError:
                    pass

        for p in roots:
            node = self.tree.insert('', 'end', text=p, values=(p,),
                                    image=self.icons['drive'], open=False)
            self.tree.insert(node, 'end', text='…')   # 占位，展开时替换

    def _on_tree_open(self, event=None):
        node = self.tree.focus()
        if node:
            self._load_tree_node(node)

    def _load_tree_node(self, node):
        """懒加载：只在该节点第一次展开时读取子目录"""
        if node in self.tree_loaded:
            return
        self.tree_loaded.add(node)

        for child in self.tree.get_children(node):
            self.tree.delete(child)

        values = self.tree.item(node, 'values')
        if not values:
            return
        path = values[0]

        try:
            with os.scandir(path) as it:
                subs = []
                for entry in it:
                    try:
                        if not entry.is_dir():
                            continue
                    except OSError:
                        continue
                    if not self.show_hidden.get() and self._is_hidden(entry):
                        continue
                    subs.append(entry)
        except OSError:
            return

        subs.sort(key=lambda e: e.name.lower())
        for entry in subs:
            child = self.tree.insert(node, 'end', text=entry.name,
                                     values=(entry.path,),
                                     image=self.icons['folder'])
            self.tree.insert(child, 'end', text='…')

    def _on_tree_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], 'values')
        if not values:
            return
        path = values[0]
        if path and os.path.isdir(path) and os.path.normpath(path) != os.path.normpath(self.current_path):
            self.navigate(path)

    # ------------------------------------------------------------------
    # 导航
    # ------------------------------------------------------------------
    def navigate(self, path, add_history=True):
        path = os.path.abspath(os.path.expanduser(str(path).strip().strip('"')))

        if not os.path.isdir(path):
            messagebox.showwarning('提示', f'路径不存在或不是文件夹：\n{path}')
            self.path_var.set(self.current_path)
            return

        try:
            with os.scandir(path):
                pass
        except PermissionError:
            messagebox.showwarning('提示', f'没有访问权限：\n{path}')
            self.path_var.set(self.current_path)
            return
        except OSError as e:
            messagebox.showwarning('提示', f'无法打开：\n{e}')
            self.path_var.set(self.current_path)
            return

        self.current_path = path
        self.path_var.set(path)
        title = os.path.basename(path) or path
        self.root.title(f'{title} — 文件资源管理器')

        if add_history:
            if not self.history or self.history[self.history_index] != path:
                del self.history[self.history_index + 1:]
                self.history.append(path)
                self.history_index = len(self.history) - 1

        self._update_nav_state()
        self._load_entries()

    def go_back(self):
        if self.history_index > 0:
            self.history_index -= 1
            self.navigate(self.history[self.history_index], add_history=False)

    def go_forward(self):
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self.navigate(self.history[self.history_index], add_history=False)

    def go_up(self):
        parent = os.path.dirname(self.current_path)
        if parent and os.path.normpath(parent) != os.path.normpath(self.current_path):
            self.navigate(parent)

    def refresh(self):
        self._load_entries()

    def _update_nav_state(self):
        self.btn_back.state(['!disabled'] if self.history_index > 0 else ['disabled'])
        self.btn_forward.state(
            ['!disabled'] if self.history_index < len(self.history) - 1 else ['disabled'])
        parent = os.path.dirname(self.current_path)
        can_up = bool(parent) and os.path.normpath(parent) != os.path.normpath(self.current_path)
        self.btn_up.state(['!disabled'] if can_up else ['disabled'])

    # ------------------------------------------------------------------
    # 读取 / 渲染文件列表
    # ------------------------------------------------------------------
    def _load_entries(self):
        self.entries = []
        try:
            with os.scandir(self.current_path) as it:
                raw = list(it)
        except OSError as e:
            self._set_status(f'无法读取目录：{e}')
            return

        show_hidden = self.show_hidden.get()
        for entry in raw:
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue

            if not show_hidden and self._is_hidden(entry):
                continue

            try:
                st = entry.stat()
                size, mtime = st.st_size, st.st_mtime
            except OSError:
                size, mtime = 0, 0.0

            self.entries.append({
                'name': entry.name,
                'path': entry.path,
                'is_dir': is_dir,
                'size': size,
                'mtime': mtime,
            })

        self._render_entries()

    def _render_entries(self):
        if not hasattr(self, 'file_list'):
            return

        for iid in self.file_list.get_children():
            self.file_list.delete(iid)
        self.iid_map = {}

        keyword = self.filter_var.get().strip().lower()
        items = self.entries
        if keyword:
            items = [i for i in items if keyword in i['name'].lower()]

        key = self.sort_key
        rev = self.sort_reverse

        def sort_key(item):
            if key == 'name':
                return item['name'].lower()
            if key == 'size':
                return item['size']
            if key == 'type':
                return self._type_of(item).lower()
            return item['mtime']

        dirs = sorted((i for i in items if i['is_dir']), key=sort_key, reverse=rev)
        files = sorted((i for i in items if not i['is_dir']), key=sort_key, reverse=rev)

        for idx, item in enumerate(dirs + files):
            iid = f'row{idx}'
            self.iid_map[iid] = item

            if item['is_dir']:
                icon = self.icons['folder']
                size_text = ''
                type_text = '文件夹'
            else:
                icon = self._icon_for(item['name'])
                size_text = format_size(item['size'])
                type_text = self._type_of(item)

            self.file_list.insert(
                '', 'end', iid=iid,
                text='  ' + item['name'],
                image=icon,
                values=(size_text, type_text, format_time(item['mtime']))
            )

        n_dirs, n_files = len(dirs), len(files)
        prefix = '筛选结果：' if keyword else '共 '
        self._set_status(f'{prefix}{n_dirs + n_files} 项（{n_dirs} 个文件夹，{n_files} 个文件）')

    # ------------------------------------------------------------------
    # 排序 / 表头
    # ------------------------------------------------------------------
    def sort_by(self, key):
        if self.sort_key == key:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = key
            self.sort_reverse = False
        self._update_headings()
        self._render_entries()

    def _update_headings(self):
        labels = {'name': '名称', 'size': '大小', 'type': '类型', 'mtime': '修改时间'}
        arrow = ' ▼' if self.sort_reverse else ' ▲'

        self.file_list.heading('#0', text=labels['name'] + (arrow if self.sort_key == 'name' else ''))
        for key in ('size', 'type', 'mtime'):
            self.file_list.heading(key, text=labels[key] + (arrow if self.sort_key == key else ''))

    # ------------------------------------------------------------------
    # 条目辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _is_hidden(entry):
        if entry.name.startswith('.'):
            return True
        if IS_WINDOWS:
            try:
                attrs = entry.stat(follow_symlinks=False).st_file_attributes
                return bool(attrs & stat.FILE_ATTRIBUTE_HIDDEN)
            except (OSError, AttributeError):
                return False
        return False

    @staticmethod
    def _type_of(item):
        ext = os.path.splitext(item['name'])[1].lower()
        if not ext:
            return '文件'
        return f'{ext[1:].upper()} 文件'

    def _icon_for(self, name):
        ext = os.path.splitext(name)[1].lower()
        if ext in CODE_EXTS:
            return self.icons['code']
        if ext in IMAGE_EXTS:
            return self.icons['image']
        if ext in ARCHIVE_EXTS:
            return self.icons['archive']
        if ext in MEDIA_EXTS:
            return self.icons['media']
        return self.icons['file']

    # ------------------------------------------------------------------
    # 交互事件
    # ------------------------------------------------------------------
    def _on_double_click(self, event=None):
        if event is not None:
            region = self.file_list.identify_region(event.x, event.y)
            if region not in ('tree', 'cell'):
                return
            iid = self.file_list.identify_row(event.y)
            if iid:
                self.file_list.selection_set(iid)
        self._open_selected()

    def _open_selected(self, event=None):
        sel = self.file_list.selection()
        if not sel:
            return
        item = self.iid_map.get(sel[0])
        if not item:
            return

        if item['is_dir']:
            self.navigate(item['path'])
        else:
            self._open_with_system(item['path'])

    @staticmethod
    def _open_with_system(path):
        try:
            if IS_WINDOWS:
                os.startfile(path)  # noqa
            elif IS_MAC:
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path])
        except Exception as e:
            messagebox.showerror('错误', f'无法打开文件：\n{e}')

    def _reveal_selected(self):
        sel = self.file_list.selection()
        if not sel:
            return
        item = self.iid_map.get(sel[0])
        if not item:
            return
        try:
            if IS_WINDOWS:
                subprocess.Popen(['explorer', '/select,', os.path.normpath(item['path'])])
            elif IS_MAC:
                subprocess.Popen(['open', '-R', item['path']])
            else:
                self.navigate(os.path.dirname(item['path']))
        except Exception as e:
            messagebox.showerror('错误', str(e))

    def _copy_path(self):
        sel = self.file_list.selection()
        if not sel:
            return
        item = self.iid_map.get(sel[0])
        if not item:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(item['path'])
        self._set_status('已复制路径到剪贴板')

    def _show_props(self):
        sel = self.file_list.selection()
        if not sel:
            return
        item = self.iid_map.get(sel[0])
        if not item:
            return

        created = '未知'
        try:
            st = os.stat(item['path'])
            created = format_time(st.st_ctime)
        except OSError:
            pass

        lines = [
            f"名称：{item['name']}",
            f"路径：{item['path']}",
            f"类型：{'文件夹' if item['is_dir'] else self._type_of(item)}",
            f"大小：{'—' if item['is_dir'] else format_size(item['size'])}",
            f"修改时间：{format_time(item['mtime'])}",
            f"创建时间：{created}",
        ]
        messagebox.showinfo('属性', '\n'.join(lines))

    def _show_menu(self, event):
        iid = self.file_list.identify_row(event.y)
        if iid:
            if iid not in self.file_list.selection():
                self.file_list.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)

    def _on_list_select(self, event=None):
        sel = self.file_list.selection()
        if len(sel) == 1:
            item = self.iid_map.get(sel[0])
            if not item:
                return
            if item['is_dir']:
                self._set_status(f"文件夹：{item['name']}")
            else:
                self._set_status(
                    f"{item['name']}  |  {format_size(item['size'])}  |  "
                    f"{format_time(item['mtime'])}"
                )
        elif len(sel) > 1:
            total = sum(self.iid_map[s]['size'] for s in sel
                        if s in self.iid_map and not self.iid_map[s]['is_dir'])
            self._set_status(f'已选中 {len(sel)} 项，文件合计 {format_size(total)}')

    def _on_path_enter(self, event=None):
        self.navigate(self.path_var.get())

    def _on_toggle_hidden(self):
        self.tree_loaded.clear()
        self._load_entries()

    def _on_backspace(self, event):
        widget = self.root.focus_get()
        if isinstance(widget, (tk.Entry, ttk.Entry)):
            return None
        self.go_up()
        return 'break'

    def _focus_path_entry(self, event=None):
        self.path_entry.focus_set()
        self.path_entry.select_range(0, 'end')
        return 'break'

    def _set_status(self, text):
        self.status_var.set(text)

def main():
    root = tk.Tk()
    try:
        # 高分屏下更清晰（Windows）
        if IS_WINDOWS:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    FileExplorer(root)
    root.mainloop()


if __name__ == '__main__':
    main()