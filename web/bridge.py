"""
异步桥：后台线程常驻事件循环
============================
Streamlit 是同步框架，每次页面交互都会重新执行脚本；而 MyAgent 业务层基于
asyncio（Agent.ask / RAGService.upload / MCPManager），且 MCP 的 stdio
子进程连接必须跨页面刷新持续存活。

解决方案：在 daemon 线程中维持一个 run_forever 的事件循环——
  - 界面线程用 asyncio.run_coroutine_threadsafe 提交协程并同步等待结果；
  - 事件循环与其中建立的 MCP 连接常驻，不受页面刷新影响。
"""
import asyncio
import threading


class AsyncBridge:
    """把协程提交到后台常驻事件循环执行，并以同步方式等待结果。"""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            name="myagent-asyncio",
            daemon=True,  # 随 Streamlit 进程退出，无需显式 join
        )
        self._thread.start()

    def run(self, coro):
        """提交协程到后台事件循环，阻塞至完成并返回结果（异常原样抛出）。"""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()
