# -*- coding: utf-8 -*-
"""
UE5 一键导出管线（Maya 版）  ue_export_maya.py   v1.0
================================================================
作  用：把 Maya 里做好的模型，按 UE5 规范一键导出成 FBX。
        自动完成：命名修正 → 冻结变换 → 清理历史 → 导出。
        原场景【不会被修改】——导出用的是临时副本。

用  法：
  1) Script Editor 打开本文件 → 全选 → Ctrl+Enter → 弹出窗口
  2) 命令行：mayapy ue_export_maya.py 场景.ma
================================================================
"""

import maya.cmds as cmds
import maya.mel as mel
import os
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ============================================================
#  配置区
# ============================================================

PREFIX = "SM_"
SANITIZE_NAMES = True
EXPORT_SCALE_FACTOR = 1.0       # Maya 与 UE 都是厘米，通常是 1.0
EXPORT_DIR = ""                 # 留空 = 场景同目录的 _UEExport
DELETE_HISTORY = True           # 导出前删除构造历史

# 说明：
#   Maya 默认单位是厘米，UE 也是厘米，所以 Scale Factor 保持 1.0 即可。
#   Maya 是 Y 轴向上，FBX 格式会做轴向转换，UE 导入后是正确的 Z 轴向上——
#   不要手动旋转模型去「对齐」，那样反而会歪。


# ============================================================
#  名称处理
# ============================================================

def sanitize_name(name, prefix=None):
    prefix = PREFIX if prefix is None else prefix
    s = re.sub(r"[^A-Za-z0-9_]", "_", name)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "Asset"
    elif s.isdigit() or len(s) <= 2:
        s = "Asset_" + s
    if prefix and not s.startswith(prefix):
        s = prefix + s
    return s


def name_lost_info(original, sanitized):
    has_cjk = any("一" <= c <= "鿿" for c in original)
    return has_cjk or len(sanitized) < len(original) * 0.4


def _unique_name(base, existing):
    if base not in existing:
        return base
    i = 1
    while "%s_%02d" % (base, i) in existing:
        i += 1
    return "%s_%02d" % (base, i)


# ============================================================
#  导出
# ============================================================

def _export_dir():
    if EXPORT_DIR:
        d = EXPORT_DIR
    else:
        scene = cmds.file(q=True, sceneName=True)
        if scene:
            d = os.path.join(os.path.dirname(scene), "_UEExport")
        else:
            d = os.path.join(os.path.expanduser("~"), "Desktop", "_UEExport")
    try:
        os.makedirs(d)
    except OSError:
        pass
    return d


def _ensure_fbx_plugin():
    try:
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            cmds.loadPlugin("fbxmaya", quiet=True)
        return True
    except Exception:
        try:
            cmds.loadPlugin("fbxmaya", quiet=True)
            return True
        except Exception as e:
            print("[ue_export_maya] FBX 插件加载失败: %s" % e)
            return False


def _export_one(transform, path):
    """导出单个 transform 为 FBX"""
    # 注意：FBX 插件的 MEL 命令布尔参数必须用 -v 形式（FBXExportInAscii false 会报语法错）
    cmds.select(transform, replace=True)
    mel.eval('FBXResetExport')
    mel.eval('FBXExportFileVersion "FBX201800"')
    mel.eval('FBXExportScaleFactor %s' % EXPORT_SCALE_FACTOR)   # 这个是裸参数，不能加 -v
    mel.eval('FBXExportInAscii -v false')
    mel.eval('FBXExportSmoothingGroups -v true')
    mel.eval('FBXExportHardEdges -v false')
    mel.eval('FBXExportTangents -v true')
    mel.eval('FBXExportSkins -v false')
    mel.eval('FBXExportShapes -v false')
    mel.eval('FBXExportSmoothMesh -v false')
    mel.eval('FBXExportInstances -v false')
    mel.eval('FBXExportReferencedAssetsContent -v false')
    mel.eval('FBXExportBakeComplexAnimation -v false')
    mel.eval('FBXExportEmbeddedTextures -v false')
    mel.eval('FBXExport -f "%s" -s' % path.replace("\\", "/"))


def export_selected(verbose=True):
    t0 = time.time()

    if not _ensure_fbx_plugin():
        return []

    sel = cmds.ls(selection=True, type="transform") or []
    # 只保留有 mesh 形状的
    objs = []
    for t in sel:
        shapes = cmds.listRelatives(t, shapes=True, type="mesh") or []
        if shapes:
            objs.append(t)

    if not objs:
        if verbose:
            print("[ue_export_maya] 没有选中带网格的物体")
        return []

    out_dir = _export_dir()
    results = []
    names_used = set()

    for obj in objs:
        raw = obj
        safe = sanitize_name(raw) if SANITIZE_NAMES else raw
        safe = _unique_name(safe, names_used)
        names_used.add(safe)

        dup = None
        try:
            # 复制成临时副本，原物体不动
            dup = cmds.duplicate(obj, name=safe, upstreamNodes=False)[0]
            # 只有存在父级时才需要提到世界空间；已在根节点上直接 parent 会报错
            if cmds.listRelatives(dup, parent=True):
                cmds.parent(dup, world=True)
            cmds.rename(dup, safe)

            # 冻结变换：缩放/旋转/位移烘进顶点
            cmds.makeIdentity(dup, apply=True, translate=True,
                              rotate=True, scale=True, normal=False)
            # 删除构造历史
            if DELETE_HISTORY:
                cmds.delete(dup, constructionHistory=True)

            path = os.path.join(out_dir, safe + ".fbx")
            _export_one(dup, path)
            results.append((raw, os.path.basename(path), True))
        except Exception as e:
            results.append((raw, str(e), False))
        finally:
            if dup and cmds.objExists(dup):
                cmds.delete(dup)

    if verbose:
        ok = sum(1 for r in results if r[2])
        print("\n" + "=" * 58)
        print("  UE 导出完成  %d/%d 个物体  耗时 %.2fs" % (ok, len(results), time.time() - t0))
        print("  输出目录: %s" % out_dir)
        print("=" * 58)
        for raw, name, good in results:
            mark = "[OK]  " if good else "[FAIL]"
            print("  %s %-24s → %s" % (mark, raw[:24], name))
            if good and raw != os.path.splitext(name)[0]:
                print("        （已改名，原物体不受影响）")
            if good and name_lost_info(raw, os.path.splitext(name)[0]):
                print("        原名含中文/特殊字符，信息已丢失，建议手动改成有意义的英文名")
        print("=" * 58 + "\n")

    return results


# ============================================================
#  Maya UI
# ============================================================

def show_ui():
    win = "ueExportWin"
    if cmds.window(win, exists=True):
        cmds.deleteUI(win)

    cmds.window(win, title="UE5 导出管线", widthHeight=(360, 280), sizeable=False)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=8, columnOffset=("both", 14))

    cmds.frameLayout(label="导出设置", collapsable=False, marginHeight=8, marginWidth=8)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=4)
    cmds.text(label="前缀：%s" % (PREFIX or "（不补）"), align="left")
    cmds.text(label="单位换算：%s" % EXPORT_SCALE_FACTOR, align="left")
    cmds.text(label="删除构造历史：%s" % ("是" if DELETE_HISTORY else "否"), align="left")
    cmds.text(label="规则可在脚本顶部配置区修改", align="left",
              font="smallObliqueLabelFont")
    cmds.setParent("..")
    cmds.setParent("..")

    cmds.button(label="导出选中物体到 UE", height=34,
                command=lambda *a: export_selected())
    cmds.button(label="打开导出文件夹", height=28,
                command=lambda *a: os.startfile(_export_dir()))

    cmds.text(label="原物体不会被修改，导出用临时副本", align="center",
              font="smallPlainLabelFont")
    cmds.setParent("..")
    cmds.showWindow(win)


if __name__ == "__main__":
    if cmds.about(batch=True):
        argv = sys.argv[1:]
        if argv and not argv[0].startswith("-"):
            scene = argv[0]
            if os.path.exists(scene):
                cmds.file(scene, open=True, force=True)
        # 命令行模式：导出全部网格
        all_meshes = cmds.ls(type="mesh", noIntermediate=True) or []
        parents = []
        for m in all_meshes:
            p = cmds.listRelatives(m, parent=True) or []
            if p:
                parents.append(p[0])
        if parents:
            cmds.select(parents, replace=True)
        export_selected()
        cmds.quit(force=True)
    else:
        show_ui()
