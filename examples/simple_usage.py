"""Prints to STDOUT:
b'<html><p>ok</p></html>'
"""

import asyncio

from lol_html import AsyncRewriter


async def main():
    rw = AsyncRewriter()

    async def produce():
        await rw.feed(b"<html><script>bad()</script><p>ok</p></html>")
        rw.close()

    async def consume():
        out = bytearray()
        async for chunk in rw:
            out += chunk
        return bytes(out)

    _, result = await asyncio.gather(produce(), consume())
    print(result)


asyncio.run(main())
