"""Async streaming HTML rewriter for Python, powered by lol_html.

This package exposes a single class, :class:`AsyncRewriter`, that drives a
long-lived Rust parser task over Tokio and integrates with ``asyncio``.

Example
-------
>>> import asyncio
>>> from lol_html import AsyncRewriter
>>>
>>> async def main():
...     rw = AsyncRewriter()
...     async def produce():
...         await rw.feed(b"<html><script>bad()</script><p>ok</p></html>")
...         rw.close()
...     async def consume():
...         out = bytearray()
...         async for chunk in rw:
...             out += chunk
...         return bytes(out)
...     _, result = await asyncio.gather(produce(), consume())
...     return result
>>>
>>> asyncio.run(main())  # doctest: +SKIP
b'<html><p>ok</p></html>'

Configuration
-------------
Defaults for all ``AsyncRewriter`` parameters are read from environment
variables at library load time:

* ``LOL_HTML_FLUSH_THRESHOLD``   (int, bytes; default 16384)
* ``LOL_HTML_FLUSH_EVERY_CHUNK`` (bool; default false)
* ``LOL_HTML_INPUT_CAPACITY``    (int; default 8)
* ``LOL_HTML_OUTPUT_CAPACITY``   (int; default 8)

All can be overridden per instance via constructor arguments.
"""

from ._lol_html import AsyncRewriter, __version__

__all__ = ["AsyncRewriter", "__version__"]
