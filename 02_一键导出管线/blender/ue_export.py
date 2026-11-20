# -*- coding: utf-8 -*-
"""
UE5 一键导出管线  ue_export.py   v1.0
================================================================
作  用：把 Blender 里做好的模型，按 UE5 的规范一键导出成 FBX。
        自动完成：命名修正 → 应用变换 → 坐标轴/单位设置 → 导出。
        原场景【不会被修改】——导出用的是临时副本。

用  法：
  1) Scripting 工作区打开本文件 → ▶ Run Script
     回 Layout，3D 视图按 N →「UE 导出」标签页
  2) 命令行：blender --background 你的文件.blend --python ue_export.py

为什么要在导出这一步做规范：
  · 命名：非法字符到 UE 里会被自动替换，不如在源头改干净
  · 变换：缩放没应用会导致尺寸失真、UV 拉伸
  · 单位：Blender 是米，UE 是厘米，处理错了模型会大 100 倍或小 100 倍
  这三件事每次都手工做，又慢又容易忘，所以写进管线。
================================================================
"""

import bpy
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

PREFIX = "SM_"                  # 自动补的命名前缀；留空则不补
SANITIZE_NAMES = True           # 自动把非法字符替换成下划线
EXPORT_SCALE_OPTION = "FBX_SCALE_ALL"   # 单位处理，见下方说明
AXIS_FORWARD = "-Y"             # UE 的前向
AXIS_UP = "Z"                   # UE 的上向
SINGLE_FILE = False             # True = 选中的物体合成一个 FBX；False = 每个物体一个 FBX
EXPORT_DIR = ""                 # 留空 = blend 同目录的 _UEExport

# 单位说明（重要）：
#   Blender 的 1 单位 = 1 米，UE 的 1 单位 = 1 厘米。
#   FBX_SCALE_ALL 会把 Blender 的场景单位一起写进 FBX，UE 导入后尺寸正确。
#   如果导入 UE 后发现模型大了/小了 100 倍，就是这里的问题，换成其他选项再试：
#     FBX_SCALE_NONE  /  FBX_SCALE_UNITS  /  FBX_SCALE_ALL
#
# 坐标轴说明：
#   UE 是 Z 轴向上、X 轴向前。Blender 是 Z 轴向上、Y 轴向前。
#   AXIS_FORWARD="-Y" 是社区验证过的 Blender→UE 设置，模型朝向不会歪。


# ============================================================
#  名称处理
# ============================================================

def sanitize_name(name, prefix=None):
    """
    把物体名变成 UE 能接受的资产名：
      1. 非 [A-Za-z0-9_] 的字符全部换成下划线
      2. 连续的多个下划线合成一个
      3. 去掉首尾下划线
      4. 补上类型前缀（如果还没有）
    """
    prefix = PREFIX if prefix is None else prefix
    s = re.sub(r"[^A-Za-z0-9_]", "_", name)
    s = re.sub(r"_+", "_", s).strip("_")
    # 中文/符号被替换后可能只剩数字（如「箱子 01」→「01」），信息全丢了。
    # 这种情况给一个可识别的占位名，避免多个资产都变成 SM_01 / SM_02 撞名。
    if not s:
        s = "Asset"
    elif s.isdigit() or len(s) <= 2:
        s = "Asset_" + s
    if prefix and not s.startswith(prefix):
        s = prefix + s
    return s


def name_lost_info(original, sanitized):
    """判断原名是否被大幅简化，用于给用户告警"""
    import unicodedata
    has_cjk = any("一" <= c <= "鿿" for c in original)
    return has_cjk or len(sanitized) < len(original) * 0.4


def _unique_name(base, existing):
    """同名时加 _01 _02 后缀，避免导出时互相覆盖"""
    if base not in existing:
        return base
    i = 1
    while "%s_%02d" % (base, i) in existing:
        i += 1
    return "%s_%02d" % (base, i)


# ============================================================
#  准备导出副本（不碰原物体）
# ============================================================

def _make_export_copy(obj, scene, name):
    """
    复制物体并把世界变换「烘」进网格数据：
      - 应用缩放/旋转/位置
      - 结果是一个变换为单位的干净网格
    """
    from mathutils import Matrix

    new = obj.copy()
    new.data = obj.data.copy()
    new.name = name
    new.data.name = name + "_Mesh"

    scene.collection.objects.link(new)

    # 把世界矩阵烘进顶点坐标，然后把物体变换归零
    mw = obj.matrix_world.copy()
    new.parent = None
    new.data.transform(mw)
    new.matrix_world = Matrix.Identity(4)

    # 更新网格包围盒
    new.data.update()
    return new


def _cleanup_temp(scene, objs):
    for o in objs:
        try:
            mesh = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if mesh and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        except Exception:
            pass


# ============================================================
#  导出
# ============================================================

def _export_dir():
    if EXPORT_DIR:
        d = bpy.path.abspath(EXPORT_DIR)
    elif bpy.data.filepath:
        d = os.path.join(os.path.dirname(bpy.data.filepath), "_UEExport")
    else:
        d = os.path.join(os.path.expanduser("~"), "Desktop", "_UEExport")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        import tempfile
        d = tempfile.mkdtemp(prefix="ue_export_")
    return d


def _do_fbx_export(filepath, selected):
    """调用 Blender 的 FBX 导出"""
    bpy.ops.object.select_all(action="DESELECT")
    for o in selected:
        o.select_set(True)
    bpy.context.view_layer.objects.active = selected[0]

    bpy.ops.export_scene.fbx(
        filepath=filepath,
        use_selection=True,
        object_types={"MESH"},
        apply_scale_options=EXPORT_SCALE_OPTION,
        axis_forward=AXIS_FORWARD,
        axis_up=AXIS_UP,
        use_mesh_modifiers=True,      # 把修改器（如细分）烘进导出结果
        mesh_smooth_type="FACE",
        bake_anim=False,
        add_leaf_bones=False,
        path_mode="AUTO",
        embed_textures=False,
    )


def export_selected(verbose=True):
    """
    主流程：选中物体 → 生成干净副本 → 逐个导出 FBX → 清理副本
    返回 [(源物体名, 导出文件名, 是否成功), ...]
    """
    t0 = time.time()
    scene = bpy.context.scene
    objs = [o for o in bpy.context.selected_objects if o.type == "MESH"]

    if not objs:
        if verbose:
            print("[ue_export] 没有选中任何网格物体")
        return []

    out_dir = _export_dir()
    results = []
    temps = []

    names_used = set()
    try:
        for obj in objs:
            raw = obj.name
            safe = sanitize_name(raw) if SANITIZE_NAMES else raw
            safe = _unique_name(safe, names_used)
            names_used.add(safe)

            tmp = _make_export_copy(obj, scene, safe)
            temp_objs = [tmp]

            # 碰撞体：如果场景里有 UCX_ + 名字 的物体，自动一起导出
            for cand in scene.objects:
                if cand.type != "MESH":
                    continue
                if cand.name.startswith("UCX_" + raw) or cand.name.startswith("UCX_" + safe):
                    ctmp = _make_export_copy(cand, scene, cand.name)
                    temp_objs.append(ctmp)

            temps.extend(temp_objs)

            path = os.path.join(out_dir, safe + ".fbx")
            try:
                _do_fbx_export(path, temp_objs)
                results.append((raw, os.path.basename(path), True))
            except Exception as e:
                results.append((raw, str(e), False))
    finally:
        _cleanup_temp(scene, temps)

    if verbose:
        ok = sum(1 for r in results if r[2])
        print("\n" + "=" * 58)
        print("  UE 导出完成  %d/%d 个物体  耗时 %.2fs" % (ok, len(results), time.time() - t0))
        print("  输出目录: %s" % out_dir)
        print("=" * 58)
        for raw, name, good in results:
            mark = "[OK]  " if good else "[FAIL]"
            print("  %s %-24s → %s" % (mark, raw[:24], name))
            if raw != os.path.splitext(name)[0] and good:
                print("        （已改名，原物体不受影响）")
            if good and name_lost_info(raw, os.path.splitext(name)[0]):
                print("        ⚠ 原名含中文/特殊字符，信息已丢失，建议手动改成有意义的英文名")
        print("=" * 58 + "\n")

    return results


# ============================================================
#  Blender UI
# ============================================================

_UI = None


def _register_ui():
    class UEEXPORT_OT_run(bpy.types.Operator):
        bl_idname = "ue_export.run"
        bl_label = "导出选中到 UE"
        bl_description = "把选中的网格按 UE5 规范导出为 FBX"

        def execute(self, context):
            export_selected()
            return {"FINISHED"}

    class UEEXPORT_PT_panel(bpy.types.Panel):
        bl_label = "UE 导出"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "UE 导出"

        def draw(self, context):
            layout = self.layout
            box = layout.box()
            box.label(text="前缀：%s" % (PREFIX or "（不补）"))
            box.label(text="坐标轴：%s / %s" % (AXIS_FORWARD, AXIS_UP))
            box.label(text="单位：%s" % EXPORT_SCALE_OPTION)
            box.label(text="选中 %d 个网格" %
                      len([o for o in context.selected_objects if o.type == "MESH"]))
            layout.operator("ue_export.run", icon="EXPORT")
            layout.separator()
            layout.label(text="导出到 blend 同目录 _UEExport", icon="INFO")

    bpy.utils.register_class(UEEXPORT_OT_run)
    bpy.utils.register_class(UEEXPORT_PT_panel)
    return (UEEXPORT_OT_run, UEEXPORT_PT_panel)


def register():
    global _UI
    if _UI:
        return
    try:
        _UI = _register_ui()
        print("[ue_export] UI 已注册：3D 视图按 N → 「UE 导出」")
    except Exception as e:
        print("[ue_export] UI 注册失败（不影响命令行）：%s" % e)


def unregister():
    global _UI
    if not _UI:
        return
    for c in reversed(_UI):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
    _UI = None


if __name__ == "__main__":
    if bpy.app.background:
        export_selected()
    else:
        register()
        export_selected()
