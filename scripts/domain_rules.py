"""MathForge V2 · S0 域级重分类规则（公开仓内容，零 LLM）。

纯函数 + 可 import。只含关键词表与判定逻辑，不写入任何题干/答案原文。

策略（确定性、可复跑）：
  1. 若记录带 category_full_path，优先用其首段映射到学科域：
       高等数学 / 高等数学-核心 -> 高数
       线性代数                -> 线代
       概率统计                -> 概率
       （历年真题 / 模拟卷 首段不表学科，落入关键词打分）
  2. 否则（无路径 / 历年真题 / 模拟卷）对 stem 做三域关键词打分，取最高分域。
  3. 无任何关键词命中 -> 返回 'unknown'（不阻塞批次，后续可目检/重分类）。

注意：本题库存在大量「仅图片」题干（stem 仅为 asset:// 引用，无 LaTeX/中文文本），
此类题干无法用关键词判定，按规则返回 'unknown'。这是诚实披露，非误判。
无分类路径组（2010-2015 数一/数三卷）按方案 §1.5 注「粗判全高数」，但因题干多为
图片、无可用文本，仍按本规则如实标 unknown，待 §8 目检/重分类补齐。
"""

CALCULUS = "高数"
LINEAR = "线代"
PROBABILITY = "概率"
UNKNOWN = "unknown"

# 路径首段 -> 学科域（直接映射，确定性）
PATH_DOMAIN = {
    "高等数学": CALCULUS,
    "高等数学-核心": CALCULUS,
    "线性代数": LINEAR,
    "概率统计": PROBABILITY,
}

# 概率域关键词（多字/符号、低误判优先）。可含通用 LaTeX 符号（非题干原文）。
PROBABILITY_KEYWORDS = [
    "概率", "分布律", "概率密度", "随机变量", "边缘分布", "联合分布",
    "条件概率", "期望", "方差", "协方差", "相关系数", "似然", "最大似然",
    "极大似然", "置信", "假设检验", "无偏", "区间估计", "样本", "抽样",
    "统计量", "正态", "泊松", "二项", "指数分布", "均匀分布", "卡方",
    "中心极限", "矩估计", "大数定律", "依概率", "频率", "标准差", "分位点",
    "P\\{", "P(", "\\sim", "分布函", "无偏估计", "有效估计",
]

# 线代域关键词（含矩阵/行列式 LaTeX 环境符号，强信号）
LINEAR_KEYWORDS = [
    "矩阵", "行列式", "向量组", "线性相关", "线性无关", "特征值", "特征向量",
    "二次型", "秩", "相似", "正交", "对称矩阵", "伴随矩阵", "逆矩阵",
    "齐次", "非齐次", "线性空间", "过渡矩阵", "对角化", "合同", "等价矩阵",
    "线性变换", "正交矩阵", "标准型", "规范形", "施密特", "基底", "维数",
    "\\begin{pmatrix}", "\\begin{bmatrix}", "\\begin{vmatrix}", "A^*",
    "|A|", "r(A)", "AX=0", "Ax=0", "基础解系", "特征多项式", "\\lambda",
]

# 高数域关键词（最广，作为多数类兜底；含积分/极限/导数等 LaTeX 符号）
CALCULUS_KEYWORDS = [
    "极限", "导数", "微分", "积分", "级数", "连续", "间断", "偏导", "拐点",
    "渐近线", "中值定理", "微分方程", "收敛", "发散", "泰勒", "洛必达",
    "切线", "法线", "曲率", "驻点", "极值", "单调", "凹凸", "函数", "曲线",
    "多元", "梯度", "方向导数", "重积分", "曲面积分", "曲线积分", "通量",
    "散度", "旋度", "向量场", "原函数", "不定积分", "定积分", "复合函数",
    "反函数", "数列", "不等式", "定义域", "可导", "可积", "偏导数", "全微分",
    "\\int", "\\lim", "f'(", "f''(", "f^{(}", "\\sum", "\\partial",
    "\\mathrm{d}", "麦克劳林", "实根", "速率", "极值点", "驻点", "凹凸性",
    "参数方程", "隐函数", "变限积分", "反常积分", "幂级数", "傅里叶",
    "\\iint", "\\iiint", "\\oint", "y''", "y'", "无穷小", "等价无穷小",
    "同阶无穷小", "高阶无穷小", "欧拉方程", "质心", "转动惯量", "引力",
    "做功", "静水", "浮力", "弧长", "旋转体", "曲面面积", "极坐标", "摆线",
    "速度", "加速度", "运动", "二重积分", "三重积分", "曲面积分", "多元函数",
]

_KEYWORD_TABLE = {
    PROBABILITY: PROBABILITY_KEYWORDS,
    LINEAR: LINEAR_KEYWORDS,
    CALCULUS: CALCULUS_KEYWORDS,
}


def _score(stem):
    s = stem or ""
    counts = {PROBABILITY: 0, LINEAR: 0, CALCULUS: 0}
    for domain, kws in _KEYWORD_TABLE.items():
        for kw in kws:
            if kw in s:
                counts[domain] += 1
    return counts


def is_image_only(stem):
    """题干是否为纯图片引用（无可用文本）。"""
    if not stem:
        return True
    s = stem.strip()
    # 去掉 markdown 图片语法 ![..](..) 后若仍无实质文本，视为纯图片
    import re
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    text = text.replace("\n", "").strip()
    return text == ""


def classify_domain(stem, category_full_path=None):
    """返回学科域：高数 / 线代 / 概率 / unknown。

    参数：
      stem: 题干文本（可为空或纯图片引用）
      category_full_path: 可选，题目分类路径（如 '线性代数 / 矩阵 / ...'）
    """
    if category_full_path:
        top = category_full_path.split(" / ")[0].strip()
        if top in PATH_DOMAIN:
            return PATH_DOMAIN[top]
    counts = _score(stem or "")
    # 优先级：概率 > 线代 > 高数（最具区分度的域优先；高数为多数类兜底）
    for d in (PROBABILITY, LINEAR, CALCULUS):
        if counts[d] > 0:
            return d
    return UNKNOWN


def domain_from_path(category_full_path):
    """仅路径映射（供需要单独看路径域的调用方使用）。"""
    if not category_full_path:
        return UNKNOWN
    top = category_full_path.split(" / ")[0].strip()
    return PATH_DOMAIN.get(top, UNKNOWN)


if __name__ == "__main__":
    import sys, json
    # 简单的自测：从 master 读入并汇报分布（不在任务书产出内，仅本地校验用）
    p = sys.argv[1] if len(sys.argv) > 1 else "cache/cxy_master.jsonl"
    try:
        import collections
        c = collections.Counter()
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                c[classify_domain(r.get("stem", ""), r.get("category_full_path"))] += 1
        print("domain distribution:", dict(c))
    except FileNotFoundError:
        print("master not found; run S0 builder first")
