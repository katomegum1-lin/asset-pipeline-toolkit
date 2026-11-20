# -*- coding: utf-8 -*-
"""
Maya 模型规范检查器  asset_lint_maya.py   v1.0
================================================================
适用：Maya 2022+（Python 3）
用  法：
  1) Maya 脚本编辑器里打开运行：
         import sys; sys.path.insert(0, r"本文件所在目录")
         import asset_lint_maya; asset_lint_maya.show_ui()
  2) 脚本编辑器直接粘贴全部内容运行 → 点工具架/窗口按钮
  3) 命令行（无需开 Maya 界面，可接入批处理）：
         mayapy asset_lint_maya.py 你的场景.ma

检查项与 Blender 版一一对应，另加 Maya 特有的两项：
  · 构造历史未清理（导出后引擎里会残留变形节点）
  · Lamina 面（完全重合的共面，是 Maya 最常见的隐藏脏面）
================================================================
"""

import maya.cmds as cmds
import csv
import os
import sys
import time
import datetime
import math

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ============================================================
#  规则配置区
# ============================================================

SCAN_MODE = "VISIBLE"        # "SELECTED" / "VISIBLE" / "ALL"
EXCLUDE_NAME_PREFIX = ("_", "$")

# ---- 命名 ----
NAME_CHECK_ENABLED = True
NAME_PREFIX_RULES = ("SM_",)
NAME_ALLOWED_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"

# ---- 规模 ----
TRIANGLE_CHECK_ENABLED = True
MAX_TRIANGLES = 50000

# ---- 几何 ----
GEOMETRY_CHECK_ENABLED = True
CHECK_LAMINA = True                # 共面重复面
CHECK_HISTORY = True               # 构造历史未清理

# ---- 法线 ----
NORMAL_CHECK_ENABLED = True
NORMAL_INWARD_WARN_RATIO = 0.30

# ---- UV ----
UV_CHECK_ENABLED = True
CHECK_UV_OVERLAP = True
UV_OVERLAP_MAX_FACES = 20000
UV_OUT_OF_RANGE_CHECK = True

# ---- 变换 ----
TRANSFORM_CHECK_ENABLED = True
SCALE_TOLERANCE = 0.001
ROTATION_TOLERANCE = 0.001

# ---- 材质 ----
MATERIAL_CHECK_ENABLED = True
MAX_MATERIAL_SLOTS = 4

# ---- 报告 ----
REPORT_DIR = ""

ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"
_LEVEL_ORDER = {ERROR: 0, WARN: 1, INFO: 2}


class Finding(object):
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
#  工具
# ============================================================

def _report_dir():
    if REPORT_DIR:
        d = REPORT_DIR
    else:
        scene = cmds.file(q=True, sceneName=True)
        if scene:
            d = os.path.join(os.path.dirname(scene), "_AssetLint")
        else:
            d = os.path.join(os.path.expanduser("~"), "Desktop", "_AssetLint")
    try:
        os.makedirs(d)
    except OSError:
        pass
    return d


def _collect_meshes():
    """返回 [(mesh形状, transform), ...]"""
    shapes = cmds.ls(type="mesh", noIntermediate=True) or []
    result = []
    for sh in shapes:
        parents = cmds.listRelatives(sh, parent=True, fullPath=False) or []
        tr = parents[0] if parents else sh
        result.append((sh, tr))

    if SCAN_MODE == "SELECTED":
        result = [r for r in result if cmds.ls(r[1], selection=True)]
    elif SCAN_MODE == "VISIBLE":
        result = [r for r in result if cmds.getAttr(r[1] + ".visibility")]

    return [r for r in result if not r[1].startswith(EXCLUDE_NAME_PREFIX)]


# ============================================================
#  检查 1：命名
# ============================================================

def check_naming(tr):
    out = []
    if NAME_PREFIX_RULES and not any(tr.startswith(p) for p in NAME_PREFIX_RULES):
        out.append(Finding(
            tr, "命名", WARN, "缺少规范前缀",
            "应以 %s 开头，例如 %s%s" % ("/".join(NAME_PREFIX_RULES), NAME_PREFIX_RULES[0], tr)))

    illegal = [c for c in tr if c not in NAME_ALLOWED_CHARS]
    if illegal:
        shown = "".join(sorted(set(illegal)))
        out.append(Finding(
            tr, "命名", ERROR,
            "含非法字符 %d 处（%s）" % (len(illegal), shown),
            "UE 只接受字母/数字/下划线；中文与空格导入时会被自动替换，导致引用断开"))
    return out


# ============================================================
#  检查 2：面数
# ============================================================

def check_triangles(mesh, tr):
    out = []
    try:
        tris = cmds.polyEvaluate(mesh, triangle=True)
    except Exception:
        return out
    if tris > MAX_TRIANGLES:
        out.append(Finding(
            tr, "面数", WARN,
            "三角面 %d 超过上限 %d" % (tris, MAX_TRIANGLES),
            "超出 %.0f%%，考虑减面或拆分" % ((tris / float(MAX_TRIANGLES) - 1) * 100)))
    return out


# ============================================================
#  检查 3：几何
# ============================================================

def check_geometry(mesh, tr):
    out = []

    # 非流形边（被 3 个以上面共享）
    try:
        nm = cmds.polyInfo(mesh, nonManifoldEdges=True)
        if nm:
            out.append(Finding(
                tr, "几何", ERROR,
                "非流形边 %d 条" % len(nm),
                "布尔/合并残留，进引擎后法线与碰撞异常；清理后重检"))
    except Exception:
        pass

    # 边界边
    try:
        border = cmds.polyInfo(mesh, borderEdges=True)
        if border:
            out.append(Finding(
                tr, "几何", INFO,
                "开放边界边 %d 条" % len(border),
                "封闭模型应补面；布料/外壳类可忽略"))
    except Exception:
        pass

    # Lamina 面（完全重合的共面）
    # 不用 polyInfo laminaFaces：它依赖网格法线数据，API 构造的重合面会漏检。
    # 自己按「顶点坐标集合」去重，只要能重合就抓得到。
    if CHECK_LAMINA:
        try:
            dups = _find_lamina(mesh)
            if dups:
                out.append(Finding(
                    tr, "几何", ERROR,
                    "重合面（Lamina）%d 组" % len(dups),
                    "例如面 %s 完全重叠；会导致 z-fighting 与烘焙错误，删掉多余面" %
                    ", ".join("%d/%d" % (a, b) for a, b in dups[:5])))
        except Exception:
            pass

    # 孤立顶点
    try:
        poly_verts = set()
        nfaces = cmds.polyEvaluate(mesh, face=True)
        for i in range(nfaces):
            v = cmds.polyListComponentConversion("%s.f[%d]" % (mesh, i), toVertex=True)
            for x in (cmds.ls(v, flatten=True) or []):
                poly_verts.add(x.split(".")[-1])
        all_v = cmds.polyEvaluate(mesh, vertex=True)
        loose = all_v - len(poly_verts)
        if loose > 0:
            out.append(Finding(
                tr, "几何", WARN,
                "游离顶点 %d 个" % loose,
                "不属于任何面，建议清理（网格 → 清理）"))
    except Exception:
        pass

    # 构造历史
    if CHECK_HISTORY:
        try:
            hist = cmds.listHistory(mesh, pruneDagObjects=True) or []
            builders = [h for h in hist if cmds.nodeType(h) not in ("mesh", "polyShape")]
            if builders:
                out.append(Finding(
                    tr, "几何", WARN,
                    "构造历史未清理（%d 个节点）" % len(builders),
                    "例如 %s；导出前执行 修改 → 冻结变换 + 删除历史" %
                    ", ".join(builders[:3])))
        except Exception:
            pass

    return out


def _find_lamina(mesh):
    """
    重合面检测：把每个面的「顶点坐标集合」作为指纹，出现两次即视为重合。
    用坐标而不是顶点索引，是为了抓住「顶点没焊死、但位置完全一致」的情况。
    """
    import maya.api.OpenMaya as om2

    sel = om2.MSelectionList()
    sel.add(mesh)
    mfn = om2.MFnMesh(sel.getDagPath(0))
    pts = mfn.getPoints()

    seen = {}
    dups = []
    for i in range(mfn.numPolygons):
        vids = mfn.getPolygonVertices(i)
        if len(vids) < 3:
            continue
        key = tuple(sorted(
            (round(pts[v].x, 4), round(pts[v].y, 4), round(pts[v].z, 4))
            for v in vids))
        if key in seen:
            dups.append((seen[key], i))
        else:
            seen[key] = i
    return dups


# ============================================================
#  检查 4：法线朝向
# ============================================================

def check_normals(mesh, tr):
    """与 Blender 版同一套启发式：封闭网格才判断，质心参考"""
    out = []
    try:
        border = cmds.polyInfo(mesh, borderEdges=True)
    except Exception:
        border = None

    if border:
        out.append(Finding(
            tr, "法线", INFO,
            "开放网格（边界边 %d 条），已跳过朝向自动判断" % len(border),
            "半成品/布料属正常"))
        return out

    try:
        import maya.api.OpenMaya as om2
        sel = om2.MSelectionList()
        sel.add(mesh)
        dag = sel.getDagPath(0)
        mfn = om2.MFnMesh(dag)

        pts = mfn.getPoints()
        if len(pts) == 0:
            return out
        cx = sum(p.x for p in pts) / len(pts)
        cy = sum(p.y for p in pts) / len(pts)
        cz = sum(p.z for p in pts) / len(pts)

        # 注意：Maya 2022 的 MFnMesh 没有批量 getPolygonNormals()，
        # 只能逐面取 getPolygonNormal(i)，这是一个版本差异坑。
        nfaces = mfn.numPolygons
        inward = 0
        total = 0
        for i in range(nfaces):
            vids = mfn.getPolygonVertices(i)
            if not vids:
                continue
            fx = sum(pts[v].x for v in vids) / len(vids)
            fy = sum(pts[v].y for v in vids) / len(vids)
            fz = sum(pts[v].z for v in vids) / len(vids)
            dx, dy, dz = fx - cx, fy - cy, fz - cz
            if dx * dx + dy * dy + dz * dz < 1e-12:
                continue
            n = mfn.getPolygonNormal(i)
            total += 1
            if n.x * dx + n.y * dy + n.z * dz < 0:
                inward += 1

        if total and inward / float(total) > NORMAL_INWARD_WARN_RATIO:
            out.append(Finding(
                tr, "法线", ERROR,
                "疑似整体反向（%d%% 的面朝内）" % (inward * 100 // total),
                "全选物体 网格显示 → 反转法线；负缩放也会导致此问题"))
    except Exception as e:
        out.append(Finding(tr, "法线", INFO, "法线检测跳过（API 异常）", str(e)))
    return out


# ============================================================
#  检查 5：UV
# ============================================================

def check_uv(mesh, tr):
    out = []

    try:
        uv_sets = cmds.polyUVSet(mesh, q=True, allUVSets=True) or []
    except Exception:
        uv_sets = []

    # 关键：Maya 允许「UV 集还在、但 UV 数据被清空」的状态（如执行过 polyMapDel）。
    # 只看 UV 集数量会漏检，必须同时确认 UV 坐标数量。
    try:
        uv_count = cmds.polyEvaluate(mesh, uvcoord=True)
    except Exception:
        uv_count = 0

    if not uv_sets or uv_count == 0:
        out.append(Finding(
            tr, "UV", ERROR, "没有任何 UV",
            "UV 集 %d 个、UV 坐标 %d 个；进引擎后无法贴图，必须重新展开" %
            (len(uv_sets), uv_count)))
        return out

    # 越界
    if UV_OUT_OF_RANGE_CHECK:
        try:
            uvs = cmds.ls(cmds.polyListComponentConversion(mesh, toUV=True), flatten=True) or []
            if uvs:
                coords = cmds.polyEditUV(uvs, q=True)
                us = coords[0::2]
                vs = coords[1::2]
                oor = sum(1 for u in us if u < -1e-6 or u > 1 + 1e-6) + \
                      sum(1 for v in vs if v < -1e-6 or v > 1 + 1e-6)
                if oor:
                    out.append(Finding(
                        tr, "UV", WARN,
                        "%d 个 UV 坐标跑出 0~1" % oor,
                        "范围 U[%.2f,%.2f] V[%.2f,%.2f]，平铺贴图可忽略" %
                        (min(us), max(us), min(vs), max(vs))))
        except Exception:
            pass

    # 重叠（自实现算法，与 Blender 版判定标准完全一致）
    if CHECK_UV_OVERLAP:
        try:
            nfaces = cmds.polyEvaluate(mesh, face=True)
            if nfaces > UV_OVERLAP_MAX_FACES:
                out.append(Finding(
                    tr, "UV", INFO,
                    "面数 %d 超过重叠检测上限，已跳过" % nfaces,
                    "调大 UV_OVERLAP_MAX_FACES 后重跑，或先拆分资产"))
            else:
                overlaps = _find_uv_overlaps_maya(mesh)
                if overlaps:
                    out.append(Finding(
                        tr, "UV", ERROR,
                        "疑似 UV 重叠 %d 处" % len(overlaps),
                        "例如面 %s；重叠会导致烘焙串色，镜像 UV 属正常需人工确认" %
                        ", ".join(str(p) for p in overlaps[:6])))
        except Exception as e:
            out.append(Finding(tr, "UV", INFO, "UV 重叠检测跳过", str(e)))
    return out


def _find_uv_overlaps_maya(mesh):
    """
    自实现 UV 重叠检测。
    为什么不用 Maya 自带的 polyUVOverlap：
      实测「空间分离但 UV 坐标完全重合」的模型，polyUVOverlap 返回 None 漏检，
      且它依赖 UV 编辑器上下文。自实现算法与 Blender 版使用同一套
      「空间哈希分桶 + 边相交 / 顶点包含」判定，保证两条管线标准统一。
    读取 UV 用 MFnMesh API，比逐面执行 cmds 命令快一个数量级。
    """
    import maya.api.OpenMaya as om2

    sel = om2.MSelectionList()
    sel.add(mesh)
    mfn = om2.MFnMesh(sel.getDagPath(0))

    polys = []
    for i in range(mfn.numPolygons):
        n = mfn.polygonVertexCount(i)
        if n < 3:
            continue
        pts = []
        for j in range(n):
            u, v = mfn.getPolygonUV(i, j)
            pts.append((u, v))
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        polys.append((i, min(xs), min(ys), max(xs), max(ys), pts))

    CELL = 0.2
    buckets = {}
    for item in polys:
        idx, minx, miny, maxx, maxy, pts = item
        x0, x1 = int(math.floor(minx / CELL)), int(math.floor(maxx / CELL))
        y0, y1 = int(math.floor(miny / CELL)), int(math.floor(maxy / CELL))
        if (x1 - x0) > 8 or (y1 - y0) > 8:
            x1, y1 = x0, y0
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                buckets.setdefault((gx, gy), []).append(item)

    found = []
    checked = set()
    for bucket in buckets.values():
        n = len(bucket)
        if n < 2:
            continue
        for i in range(n):
            for j in range(i + 1, n):
                a, b = bucket[i], bucket[j]
                key = (a[0], b[0]) if a[0] < b[0] else (b[0], a[0])
                if key in checked:
                    continue
                checked.add(key)
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
#  检查 6：变换
# ============================================================

def check_transform(tr):
    out = []
    try:
        sx, sy, sz = cmds.getAttr(tr + ".scale")[0]
    except Exception:
        return out

    if sx * sy * sz < 0:
        out.append(Finding(
            tr, "变换", ERROR,
            "存在负缩放 (%.3f, %.3f, %.3f)" % (sx, sy, sz),
            "负缩放让法线反向、碰撞体翻转，冻结变换后重检"))
    elif abs(sx - 1) > SCALE_TOLERANCE or abs(sy - 1) > SCALE_TOLERANCE or abs(sz - 1) > SCALE_TOLERANCE:
        out.append(Finding(
            tr, "变换", WARN,
            "缩放未冻结 (%.3f, %.3f, %.3f)" % (sx, sy, sz),
            "修改 → 冻结变换；否则引擎尺寸与 UV 按比例失真"))

    try:
        rx, ry, rz = cmds.getAttr(tr + ".rotate")[0]
        if abs(rx) > ROTATION_TOLERANCE or abs(ry) > ROTATION_TOLERANCE or abs(rz) > ROTATION_TOLERANCE:
            out.append(Finding(
                tr, "变换", INFO,
                "旋转未冻结 (%.1f°, %.1f°, %.1f°)" % (rx, ry, rz),
                "若模型正面朝 +Y 则冻结变换，便于引擎规范朝向"))
    except Exception:
        pass
    return out


# ============================================================
#  检查 7：材质
# ============================================================

def check_materials(mesh, tr):
    out = []
    try:
        sgs = sorted(set(cmds.listConnections(mesh, type="shadingEngine") or []))

        if not sgs:
            out.append(Finding(
                tr, "材质", ERROR, "没有指定任何材质",
                "进引擎后显示默认白模"))
            return out

        n = len(sgs)
        if n > MAX_MATERIAL_SLOTS:
            out.append(Finding(
                tr, "材质", WARN,
                "材质槽 %d 个，超过上限 %d" % (n, MAX_MATERIAL_SLOTS),
                "每个材质是一次额外 draw call，建议合并贴图"))

        # 未赋材质的面：Maya 的材质归属记录在 shadingEngine 的 sets 成员里，
        # 面级 listConnections 查不到（会全部误报）。
        # 另一个坑：sets() 返回的成员是【transform 名 + 组件】（如 SM_x.f[0:1]），
        # 不是 shape 名，所以两个名字都要比对。
        faces = cmds.polyEvaluate(mesh, face=True)
        full_covered = False
        covered = set()
        for sg in sgs:
            for m in (cmds.sets(sg, q=True) or []):
                if m == mesh or m == tr:
                    full_covered = True          # 整个物体都属于这个 SG
                elif m.startswith(mesh + ".") or m.startswith(tr + "."):
                    for e in (cmds.ls(m, flatten=True) or []):
                        # 统一归一化成 shape 名，避免 transform/shape 混算
                        covered.add(e.replace(tr + ".", mesh + "."))
        if not full_covered:
            unassigned = faces - len(covered)
            if unassigned > 0:
                out.append(Finding(
                    tr, "材质", ERROR,
                    "%d/%d 个面没有指定材质" % (unassigned, faces),
                    "这些面在引擎里会显示默认白模"))
    except Exception:
        pass
    return out


# ============================================================
#  主流程
# ============================================================

def run_lint(output_dir=None, verbose=True):
    t0 = time.time()
    meshes = _collect_meshes()
    findings = []

    for mesh, tr in meshes:
        if NAME_CHECK_ENABLED:
            findings += check_naming(tr)
        if TRIANGLE_CHECK_ENABLED:
            findings += check_triangles(mesh, tr)
        if GEOMETRY_CHECK_ENABLED:
            findings += check_geometry(mesh, tr)
        if NORMAL_CHECK_ENABLED:
            findings += check_normals(mesh, tr)
        if UV_CHECK_ENABLED:
            findings += check_uv(mesh, tr)
        if TRANSFORM_CHECK_ENABLED:
            findings += check_transform(tr)
        if MATERIAL_CHECK_ENABLED:
            findings += check_materials(mesh, tr)

    findings.sort(key=lambda f: (_LEVEL_ORDER.get(f.level, 9), f.object_name, f.check))

    out_dir = output_dir or _report_dir()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(out_dir, "MayaAssetLint_%s.csv" % stamp)
    html_path = os.path.join(out_dir, "MayaAssetLint_%s.html" % stamp)

    _write_csv(findings, csv_path)
    _write_html(findings, html_path, len(meshes), time.time() - t0)

    if verbose:
        print("\n" + "=" * 58)
        print("  Maya 模型规范检查  对象 %d 个  耗时 %.2fs" % (len(meshes), time.time() - t0))
        print("=" * 58)
        if not findings:
            print("  [OK] 全部通过")
        for f in findings:
            mark = {"ERROR": "[错误]", "WARN": "[警告]", "INFO": "[提示]"}.get(f.level, "[?]")
            print("  %s %-18s %-6s %s" % (mark, f.object_name[:18], f.check, f.message))
        print("-" * 58)
        print("  CSV : %s" % csv_path)
        print("  HTML: %s" % html_path)
        print("=" * 58 + "\n")

    return findings


def _write_csv(findings, path):
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
            "<tr><td>%s</td><td>%s</td><td style='color:%s;font-weight:600'>%s</td>"
            "<td>%s</td><td class='detail'>%s</td></tr>" % (
                _esc(f.object_name), _esc(f.check), colors.get(f.level, "#000"),
                f.level, _esc(f.message), _esc(f.detail)))
    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>Maya 模型规范检查报告</title><style>
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
</style></head><body><h1>Maya 模型规范检查报告</h1>
<div class="meta">检查对象 %d 个 · 耗时 %.2f 秒 · 生成于 %s</div>
<div class="cards">
<div class="card"><b style="color:#d32f2f">%d</b><span>错误 ERROR</span></div>
<div class="card"><b style="color:#ef6c00">%d</b><span>警告 WARN</span></div>
<div class="card"><b style="color:#1565c0">%d</b><span>提示 INFO</span></div>
</div><table><tr><th>对象</th><th>检查项</th><th>级别</th><th>问题</th><th>说明</th></tr>
%s</table></body></html>""" % (
        obj_count, elapsed, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        n_err, n_warn, n_info, "".join(rows))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ============================================================
#  Maya UI
# ============================================================

def show_ui():
    win = "assetLintWin"
    if cmds.window(win, exists=True):
        cmds.deleteUI(win)

    cmds.window(win, title="模型规范检查器", widthHeight=(360, 300), sizeable=False)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=8, columnOffset=("both", 14))

    cmds.frameLayout(label="检查设置", collapsable=False, marginHeight=8, marginWidth=8)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=4)
    cmds.text(label="扫描范围：%s" % SCAN_MODE, align="left")
    cmds.text(label="面数上限：%d" % MAX_TRIANGLES, align="left")
    cmds.text(label="材质槽上限：%d" % MAX_MATERIAL_SLOTS, align="left")
    cmds.text(label="规则可在脚本顶部配置区修改", align="left",
              font="smallObliqueLabelFont")
    cmds.setParent("..")
    cmds.setParent("..")

    cmds.button(label="运行检查", height=34, command=lambda *a: run_lint())
    cmds.button(label="打开报告文件夹", height=28,
                command=lambda *a: os.startfile(_report_dir()))

    cmds.text(label="报告输出到场景同目录 _AssetLint", align="center",
              font="smallPlainLabelFont")
    cmds.setParent("..")
    cmds.showWindow(win)


# ------------------------------------------------------------
# 命令行入口：mayapy asset_lint_maya.py 场景文件
# ------------------------------------------------------------
if __name__ == "__main__":
    if cmds.about(batch=True):
        argv = sys.argv[1:]
        if argv and not argv[0].startswith("-"):
            scene = argv[0]
            if os.path.exists(scene):
                cmds.file(scene, open=True, force=True)
        run_lint()
        cmds.quit(force=True)
    else:
        show_ui()
