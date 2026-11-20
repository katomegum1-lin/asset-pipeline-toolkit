# -*- coding: utf-8 -*-
"""
UE5 端资产导入与检查  ue_import.py   v1.0
================================================================
作  用：把建模端导出的 FBX 批量导入 UE5，并对导入后的资产做规范检查。
        补齐「建模端 → 引擎端」链路的最后一环。

用  法（任选）：
  1) 编辑器内：Tools → Execute Python Script → 选择本文件
  2) 命令行：
     UnrealEditor-Cmd.exe 你的项目.uproject -run=pythonscript
         -script="本文件路径" -unattended -nosplash
  3) 配合 Editor Utility Widget 做成按钮面板（见同目录 README）

设计说明（面试可以讲）：
  1. 建模端的检查能拦住拓扑/UV 问题，但拦不住【导入参数选错】——
     比如漏勾碰撞、材质没导入、单位没对上。这一环只能在引擎端查。
  2. 导入和检查放在同一个脚本里，是为了让「导入 → 发现不对 → 改参数重导」
     这个循环变短。分开做的话，美术往往导入完就不管了。
  3. 检查项与建模端检查器一一对应，两边用同一套报告格式，
     便于对比「建模端合格但引擎端不合格」的情况——那通常是导入参数的问题。
================================================================
"""

import unreal
import os
import csv
import datetime

# ============================================================
#  配置区
# ============================================================

# 导入源：留空则自动找项目 Saved 目录下的 AssetPipeline/FBX
SOURCE_DIR = ""

# 导入到哪个内容目录
DEST_PATH = "/Game/Imported"

# 报告输出目录（留空 = 项目 Saved/AssetPipeline）
REPORT_DIR = ""

# ---- 导入选项 ----
REPLACE_EXISTING = True          # 同名资产直接覆盖
AUTO_COLLISION = True            # 自动生成简单碰撞
COMBINE_MESHES = True            # 一个 FBX 里的多个网格合并成一个资产
IMPORT_MATERIALS = True          # 导入材质

# ---- 检查规则（与建模端保持一致）----
NAME_PREFIX_RULES = ("SM_", "SK_", "M_", "MI_", "T_")
MAX_TRIANGLES = 50000
MAX_MATERIAL_SLOTS = 4
CHECK_LOD = True
CHECK_COLLISION = True
CHECK_NAMING = True

ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"


# ============================================================
#  日志（UE 内 print 不进日志，必须用 unreal.log_*）
# ============================================================

def _log(msg):
    unreal.log_warning("[AssetPipeline] " + str(msg))


# ============================================================
#  导入
# ============================================================

def _project_saved_dir():
    """UE 返回的项目路径可能是相对的（../../），要转成绝对路径才好在资源管理器里打开"""
    try:
        return unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
    except Exception:
        return os.path.join(unreal.Paths.convert_relative_path_to_full(
            unreal.Paths.project_dir()), "Saved")


def _resolve_source_dir():
    if SOURCE_DIR:
        return SOURCE_DIR
    return os.path.join(_project_saved_dir(), "AssetPipeline", "FBX")


def _resolve_report_dir():
    if REPORT_DIR:
        d = REPORT_DIR
    else:
        d = os.path.join(_project_saved_dir(), "AssetPipeline")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def import_fbx_folder(source_dir=None, dest_path=None):
    """
    批量导入文件夹里的所有 FBX。
    返回 (成功导入的基础名列表, 跳过/失败信息列表)
    """
    source_dir = source_dir or _resolve_source_dir()
    dest_path = dest_path or DEST_PATH

    if not os.path.isdir(source_dir):
        _log("源目录不存在: " + source_dir)
        return [], ["源目录不存在: " + source_dir]

    fbx_files = sorted(
        f for f in os.listdir(source_dir) if f.lower().endswith(".fbx")
    )
    if not fbx_files:
        _log("源目录里没有 FBX: " + source_dir)
        return [], ["没有可导入的 FBX"]

    _log("发现 %d 个 FBX，开始导入到 %s" % (len(fbx_files), dest_path))

    tasks = []
    names = []
    for f in fbx_files:
        base = os.path.splitext(f)[0]
        task = unreal.AssetImportTask()
        task.filename = os.path.join(source_dir, f)
        task.destination_path = dest_path
        task.destination_name = base
        task.replace_existing = REPLACE_EXISTING
        task.automated = True
        task.save = True

        opts = unreal.FbxImportUI()
        opts.import_mesh = True
        opts.import_as_skeletal = False
        opts.import_materials = IMPORT_MATERIALS
        opts.import_textures = True
        # 5.x 的静态网格导入选项挂在 static_mesh_import_data 上
        opts.static_mesh_import_data.combine_meshes = COMBINE_MESHES
        opts.static_mesh_import_data.auto_generate_collision = AUTO_COLLISION
        opts.static_mesh_import_data.generate_lightmap_u_vs = True
        task.options = opts

        tasks.append(task)
        names.append(base)

    errors = []
    try:
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)
    except Exception as e:
        errors.append("批量导入异常: %s" % e)

    _log("导入完成，共提交 %d 个任务" % len(tasks))
    return names, errors


# ============================================================
#  检查
# ============================================================

def _asset_name(path):
    """'/Game/A/B.SM_X' -> 'SM_X'"""
    return path.rsplit("/", 1)[-1].split(".")[0]


def check_static_mesh(asset, name):
    out = []

    # 面数
    try:
        tris = asset.get_num_triangles(0)
        if tris > MAX_TRIANGLES:
            out.append((name, "面数", WARN,
                        "三角面 %d 超过上限 %d" % (tris, MAX_TRIANGLES),
                        "引擎内实测值，考虑减面或拆分"))
    except Exception:
        pass

    # 材质槽
    try:
        slots = asset.get_editor_property("static_materials")
        if len(slots) > MAX_MATERIAL_SLOTS:
            out.append((name, "材质", WARN,
                        "材质槽 %d 个，超过上限 %d" % (len(slots), MAX_MATERIAL_SLOTS),
                        "每个材质是一次额外 draw call"))
        empty = []
        for i, s in enumerate(slots):
            mi = s.get_editor_property("material_interface")
            if mi is None:
                empty.append(i)
        if empty:
            out.append((name, "材质", ERROR,
                        "空材质槽 索引 %s" % ", ".join(str(i) for i in empty),
                        "显示为默认白模，通常是导入时材质没生成成功"))
    except Exception as e:
        out.append((name, "材质", INFO, "材质槽检查跳过", str(e)))

    # LOD
    if CHECK_LOD:
        try:
            lod = unreal.EditorStaticMeshLibrary.get_lod_count(asset)
            # 实测：单个 LOD0 的资产这里返回 -1，说明没有做简化层级
            if lod <= 0:
                out.append((name, "LOD", INFO,
                            "只有单级 LOD（API 返回 %d）" % lod,
                            "近景道具可忽略；会被大量复制或远距离可见的资产建议做 LOD"))
        except Exception:
            pass

    # 碰撞
    if CHECK_COLLISION:
        try:
            bs = asset.get_editor_property("body_setup")
            has = False
            if bs is not None:
                # 有简单碰撞或者复杂碰撞都算
                try:
                    has = len(bs.get_editor_property("agg_geom")) > 0
                except Exception:
                    has = True
            if not has:
                out.append((name, "碰撞", WARN,
                            "没有碰撞体",
                            "导入时勾选 Auto Generate Collision，或手动添加"))
        except Exception:
            pass

    return out


def check_assets(path=None):
    """扫描指定内容目录下的所有资产，返回 findings 列表"""
    path = path or DEST_PATH
    findings = []

    try:
        asset_paths = unreal.EditorAssetLibrary.list_assets(path, recursive=True)
    except Exception as e:
        _log("无法列出资产: %s" % e)
        return findings

    _log("开始检查 %d 个资产" % len(asset_paths))

    for ap in asset_paths:
        name = _asset_name(ap)

        # 命名
        if CHECK_NAMING and not any(name.startswith(p) for p in NAME_PREFIX_RULES):
            findings.append((name, "命名", WARN,
                             "缺少规范前缀",
                             "应为 %s 之一" % "/".join(NAME_PREFIX_RULES)))

        try:
            asset = unreal.EditorAssetLibrary.load_asset(ap)
        except Exception:
            continue

        if isinstance(asset, unreal.StaticMesh):
            findings += check_static_mesh(asset, name)

    return findings


# ============================================================
#  报告
# ============================================================

_ORDER = {ERROR: 0, WARN: 1, INFO: 2}


def write_report(findings, out_dir=None):
    out_dir = out_dir or _resolve_report_dir()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(out_dir, "UEImportCheck_%s.csv" % stamp)
    html_path = os.path.join(out_dir, "UEImportCheck_%s.html" % stamp)

    findings = sorted(findings, key=lambda f: (_ORDER.get(f[2], 9), f[0], f[1]))

    # CSV
    try:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["资产", "检查项", "级别", "问题", "说明"])
            for row in findings:
                w.writerow(row)
    except Exception as e:
        _log("CSV 写入失败: %s" % e)

    # HTML
    try:
        colors = {ERROR: "#d32f2f", WARN: "#ef6c00", INFO: "#1565c0"}
        n_err = sum(1 for f in findings if f[2] == ERROR)
        n_warn = sum(1 for f in findings if f[2] == WARN)
        n_info = sum(1 for f in findings if f[2] == INFO)
        rows = []
        for f in findings:
            rows.append(
                "<tr><td>%s</td><td>%s</td>"
                "<td style='color:%s;font-weight:600'>%s</td>"
                "<td>%s</td><td class='detail'>%s</td></tr>" % (
                    _esc(f[0]), _esc(f[1]), colors.get(f[2], "#000"),
                    f[2], _esc(f[3]), _esc(f[4])))

        html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>UE5 资产导入检查报告</title><style>
body{font-family:"Microsoft YaHei",sans-serif;margin:32px;color:#222;background:#fafafa}
h1{font-size:22px;margin-bottom:4px}
.meta{color:#666;font-size:13px;margin-bottom:18px}
.cards{display:flex;gap:14px;margin-bottom:22px}
.card{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:14px 22px;min-width:110px}
.card b{display:block;font-size:26px;line-height:1.2}
.card span{font-size:12px;color:#777}
table{border-collapse:collapse;width:100%%;background:#fff;font-size:13px}
th{background:#37474f;color:#fff;padding:9px 10px;text-align:left;font-weight:500}
td{border-bottom:1px solid #eee;padding:8px 10px;vertical-align:top}
td.detail{color:#666}tr:hover td{background:#f5f9fc}
</style></head><body><h1>UE5 资产导入检查报告</h1>
<div class="meta">检查目录 %s · 生成于 %s</div>
<div class="cards">
<div class="card"><b style="color:#d32f2f">%d</b><span>错误 ERROR</span></div>
<div class="card"><b style="color:#ef6c00">%d</b><span>警告 WARN</span></div>
<div class="card"><b style="color:#1565c0">%d</b><span>提示 INFO</span></div>
</div><table><tr><th>资产</th><th>检查项</th><th>级别</th><th>问题</th><th>说明</th></tr>
%s</table></body></html>""" % (
            DEST_PATH, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            n_err, n_warn, n_info, "".join(rows))

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        _log("HTML 写入失败: %s" % e)

    return csv_path, html_path


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ============================================================
#  主流程
# ============================================================

def run(source_dir=None, dest_path=None):
    """导入 + 检查 + 出报告，一条龙"""
    names, errs = import_fbx_folder(source_dir, dest_path)
    for e in errs:
        _log("导入问题: " + e)

    findings = check_assets(dest_path)

    csv_path, html_path = write_report(findings)

    _log("=" * 50)
    _log("导入 %d 个，检查出 %d 条问题" % (len(names), len(findings)))
    for f in findings:
        _log("  %s | %s | %s | %s" % (f[2], f[0][:24], f[1], f[3]))
    _log("CSV : " + csv_path)
    _log("HTML: " + html_path)

    return findings


if __name__ == "__main__":
    run()
