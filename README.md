# FH6Lang

把《极限竞速：地平线 6》（Forza Horizon 6）调成 **中文 UI + 日语配音** 的小工具。

## 这是什么

游戏本身把"显示语言"和"配音语言"绑在了一起。`./media/Stripped/StringTables/` 下每个语言一个 zip，把 `CHS.zip` 和 `JP.zip` 这两个文件互换，再在 `AppData\Local\ForzaHorizon6\UserPreferredLang` 里写一个 `JP`，游戏就会以中文界面加载日语音频包。

这个工具做的就是这两件事，再加自动找游戏目录和状态记忆。

## 支持的平台和版本

- **Windows + Steam 版**
- **Windows + Xbox / Microsoft Store / Game Pass 版**
- **Steam Deck / Linux + Steam 版**（含 Proton）

## 使用方法

### Windows

到 [Releases](../../releases) 下载 `FH6Lang.exe`，双击运行。会自动弹 UAC 提权（Xbox 版可能用得上）。

或者本地装了 **Python 3.10+** 的话：

```
python fh6lang.py
```

### Steam Deck / Linux

```
chmod +x run.sh
./run.sh
```

SteamOS 默认有 Python 3，不需要装额外东西。

## 工作流程

1. 启动后选游戏版本（Steam / Xbox / 手动指定路径）
2. 自动找安装目录，找到后让你确认
3. 显示当前状态：原始 / 已互换 / 未知
4. 选"应用"或"还原"

## 命令行参数

```
python fh6lang.py --path "D:\SteamLibrary\steamapps\common\ForzaHorizon6"
python fh6lang.py --no-uac       # Windows 不要求管理员
python fh6lang.py --no-pause     # 结束不暂停（CI/脚本用）

# 非交互（任三选一互斥）
python fh6lang.py --status                  # 只打印当前状态
python fh6lang.py --apply                   # 直接应用：中文 UI + 日语配音
python fh6lang.py --apply --force           # 状态未知时强行应用
python fh6lang.py --revert                  # 还原原始语言包
```

非交互模式下不会弹版本选择菜单，会按 Steam 默认尝试自动检测；如果检测不到，请配合 `--path` 指定。

## 状态文件位置

- Windows: `%LOCALAPPDATA%\FH6Lang\state.json`
- Linux: `~/.config/fh6lang/state.json`

里面存的是互换前/后两个 zip 的 SHA-256，用来判断当前到底是原始还是已互换。

## 游戏更新后怎么办

游戏更新会重新分发原版 `CHS.zip` 和 `JP.zip`，你之前的互换被还原了。再跑一次本工具，它会发现哈希不对，按全新原始状态记录，重新互换即可。

`UserPreferredLang` 文件在用户 AppData 下，游戏更新不会动它，所以一般不用重写。

## 工作原理细节

互换 zip 用 `os.rename` 三步原子化（CHS→tmp，JP→CHS，tmp→JP），任何一步失败立即反向回滚。互换前后分别计算 SHA-256 并存进状态文件。

进程检测在 Windows 用 `tasklist`，在 Linux 用 `pgrep -af`（Proton 下 exe 名保留，所以这招能用）。排除 Launcher、EasyAntiCheat、gamelaunchhelper 这些辅助进程。

不依赖任何第三方 Python 包，只用标准库。
