"""T11 证据：真实 MCP stdio 客户端握手 + 三工具调用测试。"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402


async def main() -> int:
    params = StdioServerParameters(command=sys.executable,
                                   args=[str(Path(__file__).resolve().parent.parent / "v1" / "mcp_server.py")])
    lines = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            lines.append("== initialize + list_tools ==")
            lines.append(f"tools: {[t.name for t in tools.tools]}")

            lines.append("\n== call generate_quiz(罗尔定理, 基础, calculation) ==")
            r = await s.call_tool("generate_quiz",
                                  {"kp": "罗尔定理", "difficulty": "基础", "qtype": "calculation"})
            q = json.loads(r.content[0].text)
            lines.append(f"status={q.get('status') or 'ok'} | stem={ (q.get('statement_md') or '')[:90] }")
            lines.append(f"verify_level={q.get('verify_level')} | answer={q.get('answer_sympy')}")
            if q.get("error"):
                lines.append(f"[拦截链工作证据] 该抽样未过验证被拦下，error={q['error'][:80]}")

            lines.append("\n== call generate_quiz(拉格朗日中值定理, 基础, concept) ==")
            r = await s.call_tool("generate_quiz",
                                  {"kp": "拉格朗日中值定理", "difficulty": "基础", "qtype": "concept"})
            qy = json.loads(r.content[0].text)
            lines.append(f"status={qy.get('status') or 'ok'} | stem={ (qy.get('statement_md') or '')[:90] }")
            lines.append(f"verify_level={qy.get('verify_level')} | 双盲={((qy.get('verification') or {}).get('blind_1'))}/{((qy.get('verification') or {}).get('blind_2'))} | answer={qy.get('answer_md')}")

            lines.append("\n== call diagnose (故意答错) ==")
            stem = q.get("statement_md") or "求 f'(x)=2x 在 x=1 的值"
            std = q.get("answer_sympy") or "2"
            r2 = await s.call_tool("diagnose", {"kp": "罗尔定理", "statement_md": stem,
                                                "student_answer": "999", "standard_answer": std,
                                                "verify_level": "green"})
            d2 = json.loads(r2.content[0].text)
            lines.append(f"correct={d2['correct']} | attribution={d2['attribution']}")

            lines.append("\n== call get_mastery ==")
            r3 = await s.call_tool("get_mastery", {})
            lines.append(r3.content[0].text[:400])
    out = "\n".join(lines)
    print(out)
    Path(__file__).resolve().parent.parent.joinpath("证据/mcp_test.txt").write_text(
        "# MCP Server 实测（T11）\n\n真实 stdio 客户端（mcp SDK ClientSession）握手+调用\n\n" + out + "\n",
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
