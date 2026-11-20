# -*- coding: utf-8 -*-
"""
Blender 模型规范检查器  asset_lint.py   v1.0
================================================================
用  法（三种任选）：
  1) Blender 里：Scripting 工作区 → 打开本文件 → ▶ Run Script
     运行后 3D 视图按 N 键，在「资产检查」标签页里点按钮
  2) 命令行：blender --background 你的文件.blend --python asset_lint.py
  3) 其他脚本：import asset_lint; asset_lint.run_lint()

作  用：在模型进引擎【之前】把问题拦下来，输出 CSV + HTML 报告。

设计说明（面试可以照着讲）：
  1. 检查发生在「建模软件端」而不是引擎端 —— 问题发现得越早，返工成本越低。
     引擎端只能看到导入后的结果，此时命名、法线、UV 可能已被自动修正或破坏。
  2. 每条规则都有独立的开关和阈值，集中放在下方「规则配置区」，
     不同项目改数字就能复用，不需要动检查逻辑。
  3. 报告输出 CSV（可存档、可统计趋势）+ HTML（可发给美术看）。
  4. 所有检查都基于 bmesh 的只读分析，不修改、不保存用户的模型。
================================================================
"""

import bpy
import bmesh
import csv
import os
import sys
import time
import datetime
import math

# Windows 控制台中文输出保护
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ============================================================
#  规则配置区 —— 换项目只改这里，不用动下面的检查逻辑
# ============================================================

SCAN_MODE = "VISIBLE"        # "SELECTED" 只查选中 / "VISIBLE" 查可见 / "ALL" 查全部
EXCLUDE_NAME_PREFIX = ("_", "$")   # 物体名以此开头则跳过（下划线通常表示临时物体）

# ---- 命名 ----
NAME_CHECK_ENABLED = True
NAME_PREFIX_RULES = ("SM_",)       # 网格物体名必须以此开头（可写多个，命中其一即可）
NAME_ALLOWED_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
                                   # UE 资产名只认字母/数字/下划线，中文和空格会被自动替换

# ---- 规模 ----
TRIANGLE_CHECK_ENABLED = True
MAX_TRIANGLES = 50000              # 单个静态网格三角面上限（三渲二角色可放宽到 80000）

# ---- 几何 ----
GEOMETRY_CHECK_ENABLED = True
ZERO_AREA_TOLERANCE = 1e-8         # 面积小于此值视为退化面

# ---- 法线 ----
NORMAL_CHECK_ENABLED = True
NORMAL_INWARD_WARN_RATIO = 0.30    # 朝内法线面占比超过该值 → 报错（疑似整体反向）

# ---- UV ----
UV_CHECK_ENABLED = True
CHECK_UV_OVERLAP = True
UV_OVERLAP_MAX_FACES = 20000       # 超过此面数跳过重叠检测（避免卡死），会给出提示
UV_OUT_OF_RANGE_CHECK = True       # 检查 UV 是否跑出 0~1

# ---- 变换 ----
TRANSFORM_CHECK_ENABLED = True
SCALE_TOLERANCE = 0.001            # 缩放与 1 的偏差超过此值 → 未应用缩放
ROTATION_TOLERANCE = 0.001         # 弧度

# ---- 材质 ----
MATERIAL_CHECK_ENABLED = True
MAX_MATERIAL_SLOTS = 4             # 静态网格材质槽上限（移动端性能考虑）

# ---- 报告 ----
REPORT_DIR = ""                    # 留空 = blend 文件目录下的 _AssetLint；未保存则用桌面


# ============================================================
#  内部数据结构
# ============================================================

ERROR = "ERROR"     # 必须修，否则引擎里会出问题
WARN = "WARN"       # 建议修，影响规范统一或性能
INFO = "INFO"       # 提示，人工判断

_LEVEL_ORDER = {ERROR: 0, WARN: 1, INFO: 2}


class Finding:
    """一条检查结果"""
    __slots__ = ("object_name", "check", "level", "message", "detail")

    def __init__(self, object_name, check, level, message, detail=""):
        self.object_name = object_name
        self.check = check
        self.level = level
        self.message = message
        self.detail = detail

    def as_row(self):
        return [self.object_name, self.check, self.level, self.message, self.detail]


# ============================================================
#  工具函数
# ============================================================

def _get_report_dir():
    """决定报告输出目录，保证一定可写"""
    if REPORT_DIR:
        d = bpy.path.abspath(REPORT_DIR)
    elif bpy.data.filepath:
        d = os.path.join(os.path.dirname(bpy.data.filepath), "_AssetLint")
    else:
        d = os.path.join(os.path.expanduser("~"), "Desktop", "_AssetLint")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        import tempfile
        d = tempfile.mkdtemp(prefix="asset_lint_")
    return d


def _collect_objects():
    """按 SCAN_MODE 收集要检查的网格物体"""
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]

    if SCAN_MODE == "SELECTED":
        objs = [o for o in objs if o.select_get()]
    elif SCAN_MODE == "VISIBLE":
        objs = [o for o in objs if o.visible_get()]

    return [o for o in objs if not o.name.startswith(EXCLUDE_NAME_PREFIX)]


def _tri_count(mesh):
    """三角面数量 —— 引擎计费按三角面，不按四边面"""
    mesh.calc_loop_triangles()
    return len(mesh.loop_triangles)


# ============================================================
#  检查 1：命名
# ============================================================

def check_naming(obj):
    out = []
    name = obj.name

    if NAME_PREFIX_RULES and not any(name.startswith(p) for p in NAME_PREFIX_RULES):
        out.append(Finding(
            name, "命名", WARN,
            "缺少规范前缀",
            "应以 %s 开头，例如 %s%s" % ("/".join(NAME_PREFIX_RULES),
                                        NAME_PREFIX_RULES[0], name)
        ))

    illegal = [c for c in name if c not in NAME_ALLOWED_CHARS]
    if illegal:
        shown = "".join(sorted(set(illegal)))
        out.append(Finding(
            name, "命名", ERROR,
            "含非法字符 %d 处（%s）" % (len(illegal), shown),
            "UE 只接受字母/数字/下划线；中文与空格在导入时会被自动替换成下划线，导致引用断开"
        ))
    return out


# ============================================================
#  检查 2：面数
# ============================================================

def check_triangles(obj):
    out = []
    tris = _tri_count(obj.data)
    if tris > MAX_TRIANGLES:
        out.append(Finding(
            obj.name, "面数", WARN,
            "三角面 %d 超过上限 %d" % (tris, MAX_TRIANGLES),
            "超出 %.0f%%，考虑减面或拆分成多个资产" % ((tris / MAX_TRIANGLES - 1) * 100)
        ))
    return out


# ============================================================
#  检查 3：几何完整性（非流形 / 游离点 / 退化面）
# ============================================================

def check_geometry(obj):
    """用 bmesh 只读分析，不触碰原数据"""
    out = []
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        # 非流形边：被 3 个以上面共享，或只被 1 个面共享（开口）之外的拓扑错误
        non_manifold = [e for e in bm.edges if not e.is_manifold]
        # 细分：真正的坏边（>2 个面）与边界边（1 个面，可能是法线开口）
        bad_edges = [e for e in non_manifold if len(e.link_faces) > 2]
        boundary_edges = [e for e in non_manifold if len(e.link_faces) == 1]

        if bad_edges:
            out.append(Finding(
                obj.name, "几何", ERROR,
                "非流形边 %d 条" % len(bad_edges),
                "边被 3 个以上面共享，布尔/合并残留，进引擎后法线与碰撞会异常"
            ))
        if boundary_edges:
            out.append(Finding(
                obj.name, "几何", INFO,
                "开放边界边 %d 条" % len(boundary_edges),
                "若模型本应封闭则需补面；若是布料/外壳类可忽略"
            ))

        loose_verts = [v for v in bm.verts if not v.link_edges]
        if loose_verts:
            out.append(Finding(
                obj.name, "几何", WARN,
                "游离顶点 %d 个" % len(loose_verts),
                "不影响渲染但会增大文件，建议删除"
            ))

        zero_faces = [f for f in bm.faces if f.calc_area() < ZERO_AREA_TOLERANCE]
        if zero_faces:
            out.append(Finding(
                obj.name, "几何", ERROR,
                "退化面 %d 个" % len(zero_faces),
                "面积为 0，会导致法线计算错误和黑面"
            ))
    finally:
        bm.free()
    return out


# ============================================================
#  检查 4：法线朝向
# ============================================================

def check_normals(obj):
    """
    启发式：统计朝内的面占比，质心作为参考点。
    注意：这个判据【只对封闭网格成立】—— 开放网格（布料、外壳、半成品）
    的质心没有几何意义，强行判断会误报，所以先检测边界边，有边界就跳过。
    """
    out = []
    mesh = obj.data
    if len(mesh.polygons) == 0:
        return out

    # --- 封闭性检测：每条边被几个面使用 ---
    edge_use = {}
    for poly in mesh.polygons:
        for ek in poly.edge_keys:
            edge_use[ek] = edge_use.get(ek, 0) + 1
    open_edges = sum(1 for c in edge_use.values() if c == 1)
    if open_edges:
        out.append(Finding(
            obj.name, "法线", INFO,
            "开放网格（边界边 %d 条），已跳过朝向自动判断" % open_edges,
            "半成品/布料属正常；若应为封闭实体请补面后再检查"
        ))
        return out

    # 网格质心（用顶点平均，比包围盒中心更稳）
    if len(mesh.vertices) == 0:
        return out
    cx = sum(v.co.x for v in mesh.vertices) / len(mesh.vertices)
    cy = sum(v.co.y for v in mesh.vertices) / len(mesh.vertices)
    cz = sum(v.co.z for v in mesh.vertices) / len(mesh.vertices)

    inward = 0
    total = 0
    for poly in mesh.polygons:
        c = poly.center
        n = poly.normal
        # 面中心相对质心的方向
        dx, dy, dz = c.x - cx, c.y - cy, c.z - cz
        if dx * dx + dy * dy + dz * dz < 1e-12:
            continue
        dot = n.x * dx + n.y * dy + n.z * dz
        total += 1
        if dot < 0:
            inward += 1

    if total == 0:
        return out

    ratio = inward / total
    if ratio > NORMAL_INWARD_WARN_RATIO:
        out.append(Finding(
            obj.name, "法线", ERROR,
            "疑似整体反向（%d%% 的面朝内）" % (ratio * 100),
            "在编辑模式全选后 Shift+N 统一法线；负缩放也会导致此问题"
        ))
    return out


# ============================================================
#  检查 5：UV
# ============================================================

def check_uv(obj):
    out = []
    mesh = obj.data
    uv_layer = mesh.uv_layers.active

    if uv_layer is None:
        out.append(Finding(
            obj.name, "UV", ERROR, "没有任何 UV 图层",
            "进引擎后无法贴图，必须先展 UV"
        ))
        return out

    uvs = uv_layer.data

    # --- 越界检查 ---
    if UV_OUT_OF_RANGE_CHECK:
        out_of_range = 0
        min_u = min_v = float("inf")
        max_u = max_v = float("-inf")
        for d in uvs:
            u, v = d.uv.x, d.uv.y
            min_u = min(min_u, u); max_u = max(max_u, u)
            min_v = min(min_v, v); max_v = max(max_v, v)
            if u < -1e-6 or u > 1 + 1e-6 or v < -1e-6 or v > 1 + 1e-6:
                out_of_range += 1
        if out_of_range:
            out.append(Finding(
                obj.name, "UV", WARN,
                "%d 个 UV 顶点跑出 0~1" % out_of_range,
                "当前范围 U[%.2f,%.2f] V[%.2f,%.2f]，若使用平铺贴图可忽略" %
                (min_u, max_u, min_v, max_v)
            ))

    # --- 重叠检查（空间哈希 + 多边形相交）---
    if CHECK_UV_OVERLAP:
        nfaces = len(mesh.polygons)
        if nfaces > UV_OVERLAP_MAX_FACES:
            out.append(Finding(
                obj.name, "UV", INFO,
                "面数 %d 超过重叠检测上限，已跳过" % nfaces,
                "把 UV_OVERLAP_MAX_FACES 调大后重跑，或先拆分资产"
            ))
        else:
            overlaps = _find_uv_overlaps(mesh)
            if overlaps:
                out.append(Finding(
                    obj.name, "UV", ERROR,
                    "疑似 UV 重叠 %d 处" % len(overlaps),
                    "例如面 %s；重叠会导致烘焙串色，镜像 UV 属正常情况需人工确认" %
                    ", ".join(str(p) for p in overlaps[:6])
                ))
    return out


def _find_uv_overlaps(mesh):
    """
    UV 重叠检测：
      1) 每个面取 UV 包围盒，按网格哈希分桶 —— 只有落在同一桶的面才需要两两比较
      2) 真相交判断：边相交 + 顶点包含（射线法）
    只报告发现的前若干个，避免报告爆炸。
    """
    uv_layer = mesh.uv_layers.active
    uvs = uv_layer.data

    polys = []
    for poly in mesh.polygons:
        pts = [(uvs[li].uv.x, uvs[li].uv.y) for li in poly.loop_indices]
        if len(pts) < 3:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        polys.append((poly.index, min(xs), min(ys), max(xs), max(ys), pts))

    CELL = 0.2
    buckets = {}
    for item in polys:
        idx, minx, miny, maxx, maxy, pts = item
        x0, x1 = int(math.floor(minx / CELL)), int(math.floor(maxx / CELL))
        y0, y1 = int(math.floor(miny / CELL)), int(math.floor(maxy / CELL))
        # 大面跨太多格子时只放进第一个格子代表，避免组合爆炸
        if (x1 - x0) > 8 or (y1 - y0) > 8:
            x1, y1 = x0, y0
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                buckets.setdefault((gx, gy), []).append(item)

    found = []
    checked_pairs = set()
    for bucket in buckets.values():
        n = len(bucket)
        if n < 2:
            continue
        for i in range(n):
            for j in range(i + 1, n):
                a, b = bucket[i], bucket[j]
                key = (a[0], b[0]) if a[0] < b[0] else (b[0], a[0])
                if key in checked_pairs:
                    continue
                checked_pairs.add(key)
                # 包围盒快速排除
                if a[3] < b[1] or b[3] < a[1] or a[4] < b[2] or b[4] < a[2]:
                    continue
                if _polys_overlap(a[5], b[5]):
                    found.append(a[0])
                    if len(found) >= 20:
                        return found
    return found


def _seg_intersect(p1, p2, p3, p4):
    r_x, r_y = p2[0] - p1[0], p2[1] - p1[1]
    s_x, s_y = p4[0] - p3[0], p4[1] - p3[1]
    denom = r_x * s_y - r_y * s_x
    if abs(denom) < 1e-12:
        return False
    qp_x, qp_y = p3[0] - p1[0], p3[1] - p1[1]
    t = (qp_x * s_y - qp_y * s_x) / denom
    u = (qp_x * r_y - qp_y * r_x) / denom
    # 严格内部相交，共享边不算重叠
    return 1e-9 < t < 1 - 1e-9 and 1e-9 < u < 1 - 1e-9


def _point_in_poly(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xc = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xc:
                inside = not inside
        j = i
    return inside


def _polys_overlap(a, b):
    na, nb = len(a), len(b)
    for i in range(na):
        for j in range(nb):
            if _seg_intersect(a[i], a[(i + 1) % na], b[j], b[(j + 1) % nb]):
                return True
    return _point_in_poly(a[0], b) or _point_in_poly(b[0], a)


# ============================================================
#  检查 6：变换（缩放/旋转未应用、负缩放）
# ============================================================

def check_transform(obj):
    out = []
    sx, sy, sz = obj.scale

    if sx * sy * sz < 0:
        out.append(Finding(
            obj.name, "变换", ERROR,
            "存在负缩放 (%.3f, %.3f, %.3f)" % (sx, sy, sz),
            "负缩放会让法线反向、碰撞体翻转，Ctrl+A 应用缩放后再检查"
        ))
    elif (abs(sx - 1) > SCALE_TOLERANCE or abs(sy - 1) > SCALE_TOLERANCE
          or abs(sz - 1) > SCALE_TOLERANCE):
        out.append(Finding(
            obj.name, "变换", WARN,
            "缩放未应用 (%.3f, %.3f, %.3f)" % (sx, sy, sz),
            "Ctrl+A → 缩放；否则引擎里的尺寸与 UV 都会按比例失真"
        ))

    rx, ry, rz = obj.rotation_euler
    if abs(rx) > ROTATION_TOLERANCE or abs(ry) > ROTATION_TOLERANCE or abs(rz) > ROTATION_TOLERANCE:
        out.append(Finding(
            obj.name, "变换", INFO,
            "旋转未应用 (%.1f°, %.1f°, %.1f°)" %
            (math.degrees(rx), math.degrees(ry), math.degrees(rz)),
            "若模型的正面朝 +Y 则应用旋转，便于引擎规范朝向"
        ))
    return out


# ============================================================
#  检查 7：材质槽
# ============================================================

def check_materials(obj):
    out = []
    slots = obj.material_slots

    if len(slots) > MAX_MATERIAL_SLOTS:
        out.append(Finding(
            obj.name, "材质", WARN,
            "材质槽 %d 个，超过上限 %d" % (len(slots), MAX_MATERIAL_SLOTS),
            "每个材质槽在引擎里是一次额外的 draw call，建议合并贴图"
        ))

    empty = [i for i, s in enumerate(slots) if s.material is None]
    if empty:
        out.append(Finding(
            obj.name, "材质", ERROR,
            "空材质槽 索引 %s" % ", ".join(str(i) for i in empty),
            "进引擎后会显示为默认白模，需指定材质或删除空槽"
        ))
    return out


# ============================================================
#  主流程
# ============================================================

def run_lint(output_dir=None, verbose=True):
    """执行全部检查，返回 findings 列表"""
    t0 = time.time()

    if not GEOMETRY_CHECK_ENABLED and not UV_CHECK_ENABLED:
        pass  # 保持配置项可读性，不做短路

    objs = _collect_objects()
    findings = []

    for obj in objs:
        if NAME_CHECK_ENABLED:
            findings += check_naming(obj)
        if TRIANGLE_CHECK_ENABLED:
            findings += check_triangles(obj)
        if GEOMETRY_CHECK_ENABLED:
            findings += check_geometry(obj)
        if NORMAL_CHECK_ENABLED:
            findings += check_normals(obj)
        if UV_CHECK_ENABLED:
            findings += check_uv(obj)
        if TRANSFORM_CHECK_ENABLED:
            findings += check_transform(obj)
        if MATERIAL_CHECK_ENABLED:
            findings += check_materials(obj)

    findings.sort(key=lambda f: (_LEVEL_ORDER.get(f.level, 9), f.object_name, f.check))

    out_dir = output_dir or _get_report_dir()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(out_dir, "AssetLint_%s.csv" % stamp)
    html_path = os.path.join(out_dir, "AssetLint_%s.html" % stamp)

    _write_csv(findings, csv_path, len(objs))
    _write_html(findings, html_path, len(objs), time.time() - t0)

    if verbose:
        _print_console(findings, len(objs), time.time() - t0, csv_path, html_path)

    return findings


def _write_csv(findings, path, obj_count):
    # utf-8-sig 让 Excel 直接打开不乱码
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["对象", "检查项", "级别", "问题", "说明"])
        for x in findings:
            w.writerow(x.as_row())


def _write_html(findings, path, obj_count, elapsed):
    colors = {ERROR: "#d32f2f", WARN: "#ef6c00", INFO: "#1565c0"}
    n_err = sum(1 for f in findings if f.level == ERROR)
    n_warn = sum(1 for f in findings if f.level == WARN)
    n_info = sum(1 for f in findings if f.level == INFO)

    rows = []
    for f in findings:
        rows.append(
            '<tr><td>%s</td><td>%s</td><td style="color:%s;font-weight:600">%s</td>'
            '<td>%s</td><td class="detail">%s</td></tr>' % (
                _esc(f.object_name), _esc(f.check), colors.get(f.level, "#000"),
                f.level, _esc(f.message), _esc(f.detail))
        )

    html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>模型规范检查报告</title>
<style>
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
td.detail{color:#666}
tr:hover td{background:#f5f9fc}
</style></head><body>
<h1>模型规范检查报告</h1>
<div class="meta">检查对象 %d 个 · 耗时 %.2f 秒 · 生成于 %s</div>
<div class="cards">
<div class="card"><b style="color:#d32f2f">%d</b><span>错误 ERROR</span></div>
<div class="card"><b style="color:#ef6c00">%d</b><span>警告 WARN</span></div>
<div class="card"><b style="color:#1565c0">%d</b><span>提示 INFO</span></div>
</div>
<table><tr><th>对象</th><th>检查项</th><th>级别</th><th>问题</th><th>说明</th></tr>
%s
</table></body></html>""" % (
        obj_count, elapsed, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        n_err, n_warn, n_info, "".join(rows))

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _print_console(findings, obj_count, elapsed, csv_path, html_path):
    print("\n" + "=" * 58)
    print("  模型规范检查  对象 %d 个  耗时 %.2fs" % (obj_count, elapsed))
    print("=" * 58)
    if not findings:
        print("  ✅ 全部通过，没有发现问题")
    else:
        for f in findings:
            mark = {"ERROR": "[错误]", "WARN": "[警告]", "INFO": "[提示]"}.get(f.level, "[?]")
            print("  %s %-14s %-6s %s" % (mark, f.object_name[:14], f.check, f.message))
    print("-" * 58)
    print("  CSV : %s" % csv_path)
    print("  HTML: %s" % html_path)
    print("=" * 58 + "\n")


# ============================================================
#  Blender UI（可选）—— 运行脚本后在 3D 视图 N 面板出现
# ============================================================

def _register_ui():
    from bpy.props import StringProperty

    class ASSETLINT_OT_run(bpy.types.Operator):
        bl_idname = "asset_lint.run"
        bl_label = "运行模型检查"
        bl_description = "按规则配置扫描当前场景中的网格物体"

        def execute(self, context):
            run_lint()
            return {"FINISHED"}

    class ASSETLINT_PT_panel(bpy.types.Panel):
        bl_label = "资产检查"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "资产检查"

        def draw(self, context):
            layout = self.layout
            box = layout.box()
            box.label(text="扫描范围：%s" % SCAN_MODE)
            box.label(text="面数上限：%d" % MAX_TRIANGLES)
            box.label(text="材质槽上限：%d" % MAX_MATERIAL_SLOTS)
            layout.operator("asset_lint.run", icon="VIEWZOOM")
            layout.separator()
            layout.label(text="报告输出到 blend 同目录 _AssetLint", icon="INFO")

    bpy.utils.register_class(ASSETLINT_OT_run)
    bpy.utils.register_class(ASSETLINT_PT_panel)
    return (ASSETLINT_OT_run, ASSETLINT_PT_panel)


_UI_CLASSES = None


def register():
    global _UI_CLASSES
    if _UI_CLASSES:
        return
    try:
        _UI_CLASSES = _register_ui()
        print("[asset_lint] UI 已注册：3D 视图按 N → 「资产检查」")
    except Exception as e:
        print("[asset_lint] UI 注册失败（不影响命令行使用）：%s" % e)


def unregister():
    global _UI_CLASSES
    if not _UI_CLASSES:
        return
    for c in reversed(_UI_CLASSES):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
    _UI_CLASSES = None


# 在 Blender 里 Run Script 时 __name__ == "__main__"
if __name__ == "__main__":
    # 命令行模式（--background）直接检查并退出；图形模式注册 UI 并立即跑一次
    if bpy.app.background:
        run_lint()
    else:
        register()
        run_lint()
