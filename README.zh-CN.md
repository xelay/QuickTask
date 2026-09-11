[English](README.md) · [Русский](README.ru.md) · 中文

# QuickTask

一个笔记和任务侧边栏,以一个不起眼的小标签停靠在屏幕边缘,点击或按快捷键即可展开。

![QuickTask](screenshots/quick-task.gif)

## 主要功能

- 屏幕边缘的 36×36 像素小标签,鼠标悬停、点击或按快捷键 `Ctrl+Alt+T` 即可展开为 380 像素的侧边栏;失去焦点后会自动收起。
- 任务以普通的 `.md` 文件形式存储,带 YAML frontmatter(`done`、`archived`、日期等)——可以在 Obsidian、VS Code 或任意文本编辑器中打开。
- 通过 Watchdog 监控外部文件改动——应用外部所做的修改会被实时读取。
- 支持 Markdown(CodeMirror)且自动保存的任务描述编辑弹窗。
- 深色和浅色主题,一键切换。
- 不占用任务栏和 `Alt+Tab` 切换列表(ToolWindow 风格窗口)。
- 可选择任务存储文件夹、调整侧边栏高度,以及拖动和固定(**Pin**)小标签。

## 系统要求

Windows 10 或 11(64 位)。使用系统自带的 WebView2 组件——无需额外安装其他组件。

## 安装

从 [Releases](https://github.com/xelay/QuickTask/releases) 页面下载 `QuickTaskPortable.zip` 并运行即可,无需安装 Python 或其他依赖。

想从源码运行,或者自行打包 `.exe`?请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 操作方式

- `Ctrl+Alt+T` ——展开小标签为侧边栏,并立即创建一个新任务。该组合键可以在设置中更改或禁用。
- 将鼠标悬停在小标签的上半部分(书签图标)或点击它——侧边栏会展开;移开鼠标后会自动收起,除非用 **Pin** 按钮固定住了。
- 按住鼠标左键,在小标签的下半部分(“汉堡”图标)上下拖动——小标签会沿屏幕边缘移动位置。

## 数据存储位置

- 任务——默认存放在 `~/Documents/QuickTasks/`(可以在设置中改成任意其他文件夹)。
- 应用设置——`~/.quicktask/config.json`。
- 内部元数据缓存——`~/.quicktask/cache/<文件夹路径哈希>.sqlite3`(见下文「元数据缓存」)。

### 任务文件夹的扫描规则

QuickTask 只会读取所选文件夹根目录下的 `*.md` 文件——不会递归扫描子文件夹。其余内容一律不会被读取或改动:

- 子文件夹——可以放心地在任务旁边保留附件、图片、按年份归档的文件夹、`.obsidian` 文件夹等;
- 其他扩展名的文件;
- 隐藏文件和系统文件。

### 任务文件格式

每个任务都是一个带 YAML frontmatter 的普通 `.md` 文件:

```markdown
---
done: false
archived: false
urgent: false
created_at: 2026-09-10T12:00:00
updated_at: 2026-09-10T12:05:00
order_key: a0
---
# 任务标题

用 Markdown 编写的描述文本。
```

- `done`、`archived`、`urgent` ——状态标记。
- `created_at` / `updated_at` ——时间戳。
- `order_key` ——用于手动排序的短字符串排序键(见下文);只有被手动调整过顺序的任务才会有这个字段。
- 任务标题取自文件正文中的第一行 `# ...`;如果没有,则使用文件名。
- 您自己手动添加的其他 frontmatter 字段(标签、链接等),QuickTask 不会改动,会原样保留。

### 通过 Obsidian 或其他编辑器操作

由于任务本质上就是普通的 `.md` 文件,这个文件夹可以作为仓库(vault)在 Obsidian 中打开,也可以用任何支持 Markdown 的编辑器打开:

- 在外部(Obsidian、VS Code、记事本等)所做的修改会被实时读取——应用会监控文件夹(Watchdog),只重新读取发生变化的文件;
- `done` / `archived` / `urgent` / `created_at` / `updated_at` / `order_key` 这些字段在 Obsidian 中会显示为普通的 Properties;`done`/`archived`/`urgent` 可以放心地在外部切换,但 `order_key` 和时间戳最好不要手动修改;
- 在外部重命名文件不会破坏任务——包括排序在内的所有信息都保存在文件本身里;
- 您自己添加的其他 frontmatter 字段和 Obsidian 标签,QuickTask 不会改动,会原样保留。

### 用 Git 仓库管理

可以放心地把任务文件夹变成一个 git 仓库——在里面执行 `git init` 不会破坏任何东西:

- 常规操作(创建任务、标记完成、编辑、在列表中拖动排序)每次只会改动一个 `.md` 文件——不再有会被整体重写的共享索引文件;
- 不需要在 `.gitignore` 中额外排除什么:缓存(`~/.quicktask/cache/`)和设置(`~/.quicktask/config.json`)物理上都在任务文件夹之外,永远不会出现在里面。

### 元数据缓存(SQLite)

为了加快任务列表的操作速度,QuickTask 会维护一个内部缓存——每个配置的任务文件夹对应一个 `~/.quicktask/cache/<文件夹路径哈希>.sqlite3` 文件。这**不是数据源,只是一个用于提速、可以随时安全删除的一次性缓存**:

- 其中存放的是已经从 `.md` 文件中解析出来的数据副本,这样就不用每次操作都重新读取整个文件夹;
- 真正的数据来源永远是 `.md` 文件本身——缓存可以随时删除,应用会在下次启动时自动重新生成;
- 不需要、也没必要直接通过 SQL 读取或修改这个文件:这样做的修改不会写回 `.md` 文件,并且会在对应任务下次发生变化时被覆盖。

## 参与开发

从源码运行、打包 `.exe` 以及项目结构的说明,请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)。
