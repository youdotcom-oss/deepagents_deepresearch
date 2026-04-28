import os
from datetime import datetime
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langchain_core.tools import InjectedToolArg, tool
from langgraph.types import Overwrite
from youdotcom import You, errors
from youdotcom.models import LiveCrawl, LiveCrawlFormats, Freshness

load_dotenv()


def _join_snippets(snippets: list[str] | None) -> str:
    if not snippets:
        return ""
    return "\n".join(f"- {snippet}" for snippet in snippets)


@tool(parse_docstring=True)
def you_search(
    query: str,
    max_results: Annotated[int, InjectedToolArg] = 10,
) -> str:
    """Search the web for information on a given query.

    Uses You.com Search API with livecrawl enabled, so search results include
    full page content as Markdown (or HTML is desired).

    Args:
        query: Search query to execute.
        max_results: Maximum number of results to return, per section (web or news).

    Returns:
        Formatted search results with full page content, snippets, and URLs.
    """
    try:
        with You(api_key_auth=os.environ["YDC_API_KEY"]) as you:
            search_results = you.search.unified(
                query=query,
                count=max_results,
                freshness=Freshness.YEAR,
                livecrawl=LiveCrawl.WEB,
                livecrawl_formats=LiveCrawlFormats.MARKDOWN,
            )
    except KeyError:
        return "Missing YDC_API_KEY. Set it before running the research agent."
    except errors.YouError as exc:
        message = getattr(exc, "message", str(exc))
        status_code = getattr(exc, "status_code", None)
        status = f" (status code: {status_code})" if status_code else ""
        return f"You.com Search API error: {message}{status}"

    web_results = getattr(getattr(search_results, "results", None), "web", None) or []
    result_texts = []

    for result in web_results:
        title = getattr(result, "title", "Untitled")
        url = getattr(result, "url", "")
        description = getattr(result, "description", "")
        snippets = getattr(result, "snippets", None)
        contents = getattr(result, "contents", None)
        crawled_markdown = getattr(contents, "markdown", None) if contents else None

        body = crawled_markdown or _join_snippets(snippets) or description
        if not body:
            body = "No crawled content or snippets were returned for this result."

        result_texts.append(
            f"## {title}\n"
            f"**URL:** {url}\n"
            f"**Description:** {description}\n\n"
            f"{body}\n---"
        )

    return f"Found {len(result_texts)} result(s) for '{query}':\n\n" + "\n".join(
        result_texts
    )


RESEARCH_WORKFLOW_INSTRUCTIONS = """# Research Workflow

Follow this workflow for all research requests:

1. **Plan**: Create a todo list with write_todos to break down the research into focused tasks
2. **Save the request**: Use write_file() to save the user's research question to `/research_request.md`. If the file already exists, read it and use edit_file() to replace its contents.
3. **Research**: Delegate research tasks to sub-agents using the task() tool - ALWAYS use sub-agents for research, never conduct research yourself
4. **Synthesize**: Review all sub-agent findings and consolidate citations (each unique URL gets one number across all findings)
5. **Write Report**: Write a comprehensive final report to `/final_report.md` (see Report Writing Guidelines below). If the file already exists, read it and use edit_file() to replace its contents.
6. **Verify**: Read `/research_request.md` and confirm you've addressed all aspects with proper citations and structure

## Research Planning Guidelines
- Batch similar research tasks into a single TODO to minimize overhead
- For simple fact-finding questions, use 1 sub-agent
- For comparisons or multi-faceted topics, delegate to multiple parallel sub-agents
- Each sub-agent should research one specific aspect and return findings

## Report Writing Guidelines

When writing the final report to `/final_report.md`, follow these structure patterns:

**For comparisons:**
1. Introduction
2. Overview of topic A
3. Overview of topic B
4. Detailed comparison
5. Conclusion

**For lists/rankings:**
Simply list items with details - no introduction needed:
1. Item 1 with explanation
2. Item 2 with explanation
3. Item 3 with explanation

**For summaries/overviews:**
1. Overview of topic
2. Key concept 1
3. Key concept 2
4. Key concept 3
5. Conclusion

**General guidelines:**
- Use clear section headings (## for sections, ### for subsections)
- Write in paragraph form by default - be text-heavy, not just bullet points
- Do NOT use self-referential language ("I found...", "I researched...")
- Write as a professional report without meta-commentary
- Each section should be comprehensive and detailed
- Use bullet points only when listing is more appropriate than prose

**Citation format:**
- Cite sources inline using [1], [2], [3] format
- Assign each unique URL a single citation number across ALL sub-agent findings
- End report with ### Sources section listing each numbered source
- Number sources sequentially without gaps (1,2,3,4...)
- Format: [1] Source Title: URL (each on separate line for proper list rendering)
- Example:

 Some important finding [1]. Another key insight [2].

 ### Sources
 [1] AI Research Paper: https://example.com/paper
 [2] Industry Analysis: https://example.com/analysis
"""


RESEARCHER_INSTRUCTIONS = """You are a research assistant conducting research on the user's input topic. For context, today's date is {date}.

Your job is to use tools to gather information about the user's input topic.
You can use the you_search tool to find resources that can help answer the research question.
You can call it in series or in parallel, your research is conducted in a tool-calling loop.

You have access to the you_search tool for conducting web searches. It uses You.com Search API with livecrawl enabled, so each result can include full page Markdown content without a separate webpage fetch step.

Think like a human researcher with limited time. Follow these steps:

1. **Read the question carefully** - What specific information does the user need?
2. **Start with broader searches** - Use broad, comprehensive queries first
3. **After each search, pause and assess** - Do I have enough to answer? What's still missing?
4. **Execute narrower searches as you gather information** - Fill in the gaps
5. **Stop when you can answer confidently** - Don't keep searching for perfection

**Tool Call Budgets** (Prevent excessive searching):
- **Simple queries**: Use 2-3 search tool calls maximum
- **Complex queries**: Use up to 5 search tool calls maximum
- **Always stop**: After 5 search tool calls if you cannot find the right sources

**Stop Immediately When**:
- You can answer the user's question comprehensively
- You have 3+ relevant examples/sources for the question
- Your last 2 searches returned similar information

After each search, assess results before continuing: What key information did I find? What's missing? Do I have enough to answer? Should I search more or provide my answer?

When providing your findings back to the orchestrator:

1. **Structure your response**: Organize findings with clear headings and detailed explanations
2. **Cite sources inline**: Use [1], [2], [3] format when referencing information from your searches
3. **Include Sources section**: End with ### Sources listing each numbered source with title and URL

Example:
## Key Findings

Context engineering is a critical technique for AI agents [1]. Studies show that proper context management can improve performance by 40% [2].

### Sources
[1] Context Engineering Guide: https://example.com/context-guide
[2] AI Performance Study: https://example.com/study

The orchestrator will consolidate citations from all sub-agents into the final report.
"""


SUBAGENT_DELEGATION_INSTRUCTIONS = """# Sub-Agent Research Coordination

Your role is to coordinate research by delegating tasks from your TODO list to specialized research sub-agents.

## Delegation Strategy

**DEFAULT: Start with 1 sub-agent** for most queries:
- "What is quantum computing?" -> 1 sub-agent (general overview)
- "List the top 10 coffee shops in San Francisco" -> 1 sub-agent
- "Summarize the history of the internet" -> 1 sub-agent
- "Research context engineering for AI agents" -> 1 sub-agent (covers all aspects)

**ONLY parallelize when the query EXPLICITLY requires comparison or has clearly independent aspects:**

**Explicit comparisons** -> 1 sub-agent per element:
- "Compare OpenAI vs Anthropic vs DeepMind AI safety approaches" -> 3 parallel sub-agents
- "Compare Python vs JavaScript for web development" -> 2 parallel sub-agents

**Clearly separated aspects** -> 1 sub-agent per aspect (use sparingly):
- "Research renewable energy adoption in Europe, Asia, and North America" -> 3 parallel sub-agents (geographic separation)
- Only use this pattern when aspects cannot be covered efficiently by a single comprehensive search

## Key Principles
- **Bias towards single sub-agent**: One comprehensive research task is more token-efficient than multiple narrow ones
- **Avoid premature decomposition**: Don't break "research X" into "research X overview", "research X techniques", "research X applications" - just use 1 sub-agent for all of X
- **Parallelize only for clear comparisons**: Use multiple sub-agents when comparing distinct entities or geographically separated data

## Parallel Execution Limits
- Use at most {max_concurrent_research_units} parallel sub-agents per iteration
- Make multiple task() calls in a single response to enable parallel execution
- Each sub-agent returns findings independently

## Research Limits
- Stop after {max_researcher_iterations} delegation rounds if you haven't found adequate sources
- Stop when you have sufficient information to answer comprehensively
- Bias towards focused research over exhaustive exploration"""


max_concurrent_research_units = 3
max_researcher_iterations = 3

current_date = datetime.now().strftime("%Y-%m-%d")

INSTRUCTIONS = (
    RESEARCH_WORKFLOW_INSTRUCTIONS
    + "\n\n"
    + "=" * 80
    + "\n\n"
    + SUBAGENT_DELEGATION_INSTRUCTIONS.format(
        max_concurrent_research_units=max_concurrent_research_units,
        max_researcher_iterations=max_researcher_iterations,
    )
)

research_sub_agent = {
    "name": "research-agent",
    "description": "Delegate research to the sub-agent. Give one topic at a time.",
    "system_prompt": RESEARCHER_INSTRUCTIONS.format(date=current_date),
    "tools": [you_search],
}

model = ChatAnthropic(model="claude-sonnet-4-6", temperature=0.0)
backend = FilesystemBackend(root_dir=Path.cwd(), virtual_mode=True)

agent = create_deep_agent(
    model=model,
    tools=[you_search],
    system_prompt=INSTRUCTIONS,
    subagents=[research_sub_agent],
    backend=backend,
)


if __name__ == "__main__":
    question = os.environ.get(
        "RESEARCH_QUESTION",
        "What are the main differences between RAG and fine-tuning for LLM applications?",
    )
    for chunk in agent.stream(
        {"messages": [HumanMessage(content=question)]},
        stream_mode="updates",
    ):
        for node, update in chunk.items():
            if not update or not (messages := update.get("messages")):
                continue
            msg_list = messages.value if isinstance(messages, Overwrite) else messages
            for msg in msg_list:
                if hasattr(msg, "content") and msg.content:
                    print(msg.content)
