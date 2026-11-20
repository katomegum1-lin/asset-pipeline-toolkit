# UE5 端：批量导入与检查

> 补齐「建模端 → 引擎端」的最后一环。
> 文件：`ue_import.py` · 适用 UE 5.4+

---

## 它解决什么

建模端的检查器能拦住拓扑、UV、命名问题，但**拦不住导入参数选错**：

- 漏勾自动碰撞 → 模型掉进地面
- 材质没导入 → 一片白模
- 单位设置错 → 大了 100 倍
- 忘了做 LOD → 远端场景掉帧

这些只有进了引擎才看得见。这个脚本把「导入 + 检查」合成一步，
让「导入 → 发现不对 → 改参数重导」的循环变短。

---

## 三种用法

### 方式一：菜单操作（推荐，不用敲命令）

1. 把建模端导出的 FBX 全部放进：
   ```
   你的UE项目\Saved\AssetPipeline\FBX\
   ```
   （文件夹不存在就自己建一个）

2. UE 编辑器顶部菜单 → **Tools → Execute Python Script**
3. 选择 `ue_import.py`

完成后报告会自动生成，路径在 Output Log 里能看到。

> 注意：**编辑器里执行**用的是 GUI 方式；下面的命令行方式适合批处理。

### 方式二：命令行（批量 / 自动化）

```bash
UnrealEditor-Cmd.exe "你的项目.uproject" -run=pythonscript ^
    -script="F:\...\ue_import.py" -unattended -nosplash
```

### 方式三：Editor Utility Widget 面板（进阶，做成按钮）

见下方「附录：EUW 面板搭建」。

---

## 配置

打开 `ue_import.py`，顶部「配置区」：

```python
SOURCE_DIR = ""              # FBX 来源文件夹，留空 = 项目 Saved/AssetPipeline/FBX
DEST_PATH = "/Game/Imported" # 导入到哪个内容目录
REPLACE_EXISTING = True      # 同名资产覆盖
AUTO_COLLISION = True        # 自动生成简单碰撞
COMBINE_MESHES = True        # 多个网格合并成一个资产
MAX_TRIANGLES = 50000        # 与建模端保持一致
MAX_MATERIAL_SLOTS = 4
```

**建议**：`DEST_PATH` 改成你自己的目录，比如 `/Game/Props/Street`。

---

## 检查项

| 检查项 | 级别 | 说明 |
|---|---|---|
| 命名前缀 | 警告 | 是否为 SM_ / SK_ / M_ / MI_ / T_ |
| 三角面超预算 | 警告 | 引擎内实测值 |
| 材质槽超过 4 个 | 警告 | draw call |
| 空材质槽 | 错误 | 显示白模，通常是导入失败 |
| 没有 LOD | 提示 | 近景道具可忽略 |
| 没有碰撞体 | 警告 | 导入时没勾自动碰撞 |

报告输出到 **项目 `Saved/AssetPipeline/`**，CSV + HTML 两种格式，
**排版与建模端检查器完全一致**——方便对比「建模端合格但引擎端不合格」的情况。

---

## 实测数据

在本机 UE 5.8 上验证：

- 3 个 FBX 批量导入 → 4 个资产（3 网格 + 1 材质）
- 检查出 4 条问题（1 个命名警告 + 3 个 LOD 提示）
- 单次全流程约 20 秒（含编辑器启动）

---

## 附录：EUW 面板搭建（可视化按钮）

如果你想把「导入 + 检查」做成一个点按钮就执行的面板：

### 第 1 步：创建 Widget

1. 内容浏览器空白处右键 → **Editor Utilities → Editor Utility Widget**
2. 命名 `EUW_AssetPipeline`
3. 双击打开

### 第 2 步：搭界面

1. 左侧 **Palette** 面板里搜 `Button`，拖两个到设计区
2. 再搜 `Text`，拖两个放在按钮上方做标题
3. 选中第一个按钮 → 右侧 **Details** 面板：
   - `Content` 改成「批量导入」
   - `Padding` 设成 `8, 4`
4. 第二个按钮改成「只检查」（可选）

### 第 3 步：接事件

1. 选中「批量导入」按钮，Details 面板最下面找 **Events → On Clicked**
2. 点右边的 **+**，自动跳到 Graph 模式
3. 在 Graph 里右键搜 **Execute Console Command**
4. 节点里填：
   ```
   py "F:/CherryStudio输出/资产管线工具包/03_UE5导入与检查/ue_import.py"
   ```
   （路径换成你自己电脑上的实际路径，**用正斜杠 /**）
5. 编译保存

### 第 4 步：运行

内容浏览器里右键 `EUW_AssetPipeline` → **Run Editor Utility Widget**，
面板就停靠在编辑器里了。

> **注意**：`Execute Console Command` 需要编辑器开着才能用。
> 它的效果和菜单里的 Tools → Execute Python Script 完全一样，
> 只是从「每次选文件」变成「点一下按钮」。

---

## 和前面工具的关系

```
建模端（Blender / Maya）
   ↓  asset_lint.py / asset_lint_maya.py    ← 查出拓扑、UV、命名问题
   ↓  ue_export.py / ue_export_maya.py      ← 修正命名、烘焙变换、导出 FBX
   ↓
UE5 端
   ↓  ue_import.py                          ← 批量导入 + 查导入参数问题
```

三段都过一遍，资产基本不会在「交付后」才发现问题。
