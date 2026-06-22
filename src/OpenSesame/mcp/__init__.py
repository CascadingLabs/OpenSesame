"""OpenSesame MCP — a solver-on-tap exposed over the Model Context Protocol.

The server (``OpenSesame.mcp.server``) lets an agent solve a captcha on a browser
tab it is **already** driving (via VoidCrawl MCP, Playwright MCP, or any CDP
browser): it attaches to the shared Chrome, adopts the exact tab, solves in
place, and detaches — leaving the token/answer in the agent's tab.
"""
