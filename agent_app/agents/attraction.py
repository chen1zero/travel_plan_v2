"""LLM-powered attraction search specialist."""

from datetime import date
import json
import re
from typing import Optional

from agent_app.agents.base import (
    LLM,
    MAX_AGENT_ITERATIONS,
    SimpleAgent,
)

ATTRACTION_SEARCH_SYSTEM_PROMPT = """\
你是 AttractionSearchAgent，一名景点搜索专家。

你的任务：
1. 从用户需求中提取目的地城市和景点偏好。
2. 把偏好转换成适合 POI 搜索的关键词，例如：
   - 历史文化 -> “博物馆 古迹 历史建筑”
   - 自然风光 -> “风景名胜 公园 自然景区”
   - 亲子 -> “动物园 科技馆 主题乐园”
3. 必须调用 maps_text_search，并且只能根据工具结果推荐景点。

修订模式：
- 输入可能是 mode=revision 的 JSON，包含 current_request、change_reasons、
  previous_specialist_result 和 historical_plan_context。
- 只有 Harness 判定景点条件变化时才会调用你。必须结合历史结果理解本轮
  变化，保留仍符合要求的候选，只搜索需要新增或替换的景点，不要假装没有
  看过上一版。
- 用户要求“多加/增加/安排一个景点”时，必须提供至少一个未出现在历史日程
  中的新候选；要求夜景时，搜索关键词和推荐理由必须明确匹配夜间观景需求。
- 候选景点按 name 去重，并提供足够的不同候选供多日行程使用；不得把同一
  景点改写名称后重复返回。
- 回复仍需输出完整景点研究 JSON，供下一轮继续复用。

工具调用格式（参数来自高德 MCP tools/list）：
maps_text_search({
  "keywords": "博物馆 古迹",
  "city": "北京",
  "citylimit": "true"
})
- keywords：必填字符串，使用简洁 POI 关键词，不要写成长句。
- city：可选字符串；已识别城市时必须传入。
- citylimit：字符串，不是布尔值；限定城市时传 "true"，否则传 "false"。
- 如果第一组关键词结果明显不足，可以更换关键词再次调用，但不要重复相同查询。

回复要求：
- 只输出一个合法 JSON 对象，不要输出 Markdown、代码围栏或额外说明。
- 按与偏好的相关性排序；候选数量必须满足用户消息中给出的最低数量，且最多
  返回 40 个景点。
- name、address、type、poi_id、location 只能来自工具结果；缺失时使用 null。
- location 有值时拆分为 longitude 和 latitude 数字，不要保留为逗号字符串。
- recommendation_reason 可以根据用户偏好解释，但不能补充工具未提供的事实。
- 不要生成行程、路线、预算、门票、开放时间或酒店建议。

下面仅是结构示例；所有示例值必须替换为本次输入和工具返回的真实值。
严格按照以下 JSON 结构回复：
{
  "city": "北京",
  "preferences": ["历史文化"],
  "search_query": {
    "keywords": "博物馆 古迹",
    "city": "北京",
    "citylimit": "true"
  },
  "attractions": [
    {
      "name": "示例景点",
      "address": "示例地址",
      "type": "博物馆",
      "typecode": "140100",
      "poi_id": null,
      "location": {
        "longitude": 116.397,
        "latitude": 39.918
      },
      "recommendation_reason": "符合历史文化偏好"
    }
  ],
  "data_notes": []
}
"""


class AttractionSearchAgent(SimpleAgent):
    """Use an independent LLM loop to search attractions by preference."""

    def __init__(
        self,
        llm: LLM,
        max_iterations: int = MAX_AGENT_ITERATIONS,
    ) -> None:
        super().__init__(
            llm=llm,
            system_prompt=ATTRACTION_SEARCH_SYSTEM_PROMPT,
            name="AttractionSearchAgent",
            max_iterations=max_iterations,
            required_tool_names=("maps_text_search",),
            required_output_fields=(
                "city",
                "preferences",
                "search_query",
                "attractions",
                "data_notes",
            ),
        )
        self._minimum_candidate_count = 1

    def run(self, query: str) -> str:
        """Extract attraction preferences and search matching POIs."""
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query 不能为空")
        self._minimum_candidate_count = min(
            40, _requested_day_count(normalized_query) * 2
        )
        return super().run(
            f"旅行需求：{normalized_query}\n"
            f"至少返回 {self._minimum_candidate_count} 个不同景点候选，"
            "以保证每天至少有两个不重复候选可供 Planner 取舍。"
        )

    def _output_validation_error(self, value: str) -> Optional[str]:
        base_error = super()._output_validation_error(value)
        if base_error is not None:
            return base_error
        parsed = json.loads(value)
        attractions = parsed.get("attractions")
        if not isinstance(attractions, list):
            return "attractions 必须是数组"
        unique_names = {
            str(item.get("name") or "").strip()
            for item in attractions
            if isinstance(item, dict)
        }
        unique_names.discard("")
        if len(unique_names) < self._minimum_candidate_count:
            return (
                "不同景点候选不足：至少需要 "
                f"{self._minimum_candidate_count} 个"
            )
        return None


def _requested_day_count(query: str) -> int:
    """Infer the inclusive trip length from structured ISO dates."""
    parsed_dates = []
    for value in re.findall(r"\d{4}-\d{2}-\d{2}", query):
        try:
            parsed_dates.append(date.fromisoformat(value))
        except ValueError:
            continue
    if len(parsed_dates) < 2:
        return 1
    days = (parsed_dates[1] - parsed_dates[0]).days + 1
    return max(1, min(days, 31))
