from ddgs import DDGS


def web_search(query, max_results=5):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        return f"Search failed: {e}"
    if not results:
        return f"No results found for '{query}'."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n{r['body']}\n{r['href']}")
    return "\n\n".join(lines)
