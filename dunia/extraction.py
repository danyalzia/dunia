# MIT License

# Copyright (c) 2022 Danyal Zia Khan

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from typing import TYPE_CHECKING, cast, overload

import backoff
import lxml.html as lxml
from selectolax.lexbor import LexborHTMLParser
from selectolax.parser import HTMLParser
from throttler import throttle

from dunia.aio import with_timeout
from dunia.error import (
    HTMLParsingError,
    PlaywrightError,
    PlaywrightTimeoutError,
    TimeoutException,
    backoff_hdlr,
)
from dunia.lexbor import LexborDocument
from dunia.log import debug
from dunia.lxml import LXMLDocument
from dunia.modest import ModestDocument

if TYPE_CHECKING:
    from typing import Literal

    from dunia.browser import Browser
    from dunia.html import HTML
    from dunia.page import Page


# ? Sometimes websites are throwing JavaScript exceptions in devtools console, which makes the page stuck on "networkidle", so let's make "load" by default for now
@backoff.on_exception(
    backoff.expo,
    TimeoutException,
    max_tries=5,
    on_backoff=backoff_hdlr,
)
async def visit_link(
    page: Page,
    product_href: str,
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "load",
):
    """
    Visit the page (url) and retry for 5 times if the navigation has been failed within the configured timeout
    """
    try:
        await page.goto(product_href, wait_until=wait_until)
    except (PlaywrightTimeoutError, PlaywrightError) as err:
        raise TimeoutException(err) from err


async def load_html(
    html: HTML,
):
    """
    Utility function to load the html page with "None" type checking/narrowing
    """
    return await html.load() if await html.exists() else None


async def load_content(
    *,
    browser: Browser,
    url: str,
    html: HTML,
    page_throttle_rate_limit: int = 10,
    async_timeout: int = 600,
    save_html: bool = True,
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "load",
    strict: bool = False,
):
    """
    Load HTML content

    Read from the file if it exists on disk, otherwise fetch it with Browser using HTTP's GET request

    If the request fails and 'strict' is False, then visit the URL
    """
    if save_html:
        if await html.exists():
            debug(f"Loading content from existing HTML: {html.file}")
            content = await html.load()
        else:
            try:
                debug(f"Fetching content from URL: {url}")
                content = await fetch_content(browser, url, page_throttle_rate_limit)
            except UnicodeDecodeError as err:
                if strict:
                    raise err from err

                debug(
                    f'Fetching fails due to an error -> "{err}", visiting the URL ({url}) ...'
                )
                visit = with_timeout(async_timeout)(  # type: ignore
                    throttle(rate_limit=page_throttle_rate_limit, period=1.0)(  # type: ignore
                        visit_link
                    )
                )
                page = await browser.new_page()
                await visit(page, url, wait_until=wait_until)
                content = await page.content()
                await page.close()
            await html.save(content)
    else:
        try:
            debug(f"Fetching content from URL: {url}")
            content = await fetch_content(browser, url, page_throttle_rate_limit)
        except UnicodeDecodeError as err:
            if strict:
                raise err from err

            debug(
                f'Fetching fails due to an error -> "{err}", visiting the URL ({url}) ...'
            )
            visit = with_timeout(async_timeout)(  # type: ignore
                throttle(rate_limit=page_throttle_rate_limit, period=1.0)(  # type: ignore
                    visit_link
                )
            )
            page = await browser.new_page()
            await visit(page, url, wait_until=wait_until)
            content = await page.content()
            await page.close()

    return content


async def reload_content(
    *,
    browser: Browser,
    url: str,
    html: HTML,
    page_throttle_rate_limit: int = 10,
    async_timeout: int = 600,
    save_html: bool = True,
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "load",
    strict: bool = False,
):
    """
    Reload HTML content

    Remove the file if it exists on disk and fetch it again with Browser using HTTP's GET request

    If the request fails and 'strict' is False, then visit the URL
    """
    if save_html:
        with suppress(OSError):
            os.remove(html.file)

    try:
        debug(f"Fetching content from URL: {url}")
        content = await fetch_content(browser, url, page_throttle_rate_limit)
    except UnicodeDecodeError as err:
        if strict:
            raise err from err

        debug(
            f'Fetching fails due to an error -> "{err}", visiting the URL ({url}) ...'
        )
        visit = with_timeout(async_timeout)(  # type: ignore
            throttle(rate_limit=page_throttle_rate_limit, period=1.0)(  # type: ignore
                visit_link
            )
        )
        page = await browser.new_page()
        await visit(page, url, wait_until=wait_until)
        content = await page.content()
        await page.close()

    if save_html:
        await html.save(content)

    return content


@backoff.on_exception(
    backoff.expo,
    TimeoutException,
    max_tries=5,
    on_backoff=backoff_hdlr,
)
async def fetch_content(browser: Browser, url: str, rate_limit: int):
    """
    Using the Browser, send HTTP's GET request and receive the content response
    """
    get = throttle(rate_limit=rate_limit, period=1.0)(  # type: ignore
        browser.request.get
    )

    try:
        body = cast(bytes, await (await get(url)).body())
    except (PlaywrightTimeoutError, PlaywrightError) as err:
        raise TimeoutException(err) from err

    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError as err:
        try:
            return body.decode(
                "utf-16",
            )
        except UnicodeDecodeError as err:
            try:
                return body.decode("cp1252")
            except UnicodeDecodeError as err:
                raise err from err


async def load_page(
    *,
    browser: Browser,
    url: str,
    html: HTML,
    page_throttle_rate_limit: int = 10,
    async_timeout: int = 600,
    save_html: bool = True,
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "load",
):
    """
    Create a new page in the browser and visit the URL

    Save the HTML content of page in the file on disk
    """
    if save_html:
        if await html.exists():
            debug(f"Loading content from existing HTML: {html.file}")
            content = await html.load()
            page = await browser.new_page()
            await page.set_content(content, wait_until=wait_until)
        else:
            debug(f"Visiting the URL ({url}) ...")
            visit = with_timeout(async_timeout)(  # type: ignore
                throttle(rate_limit=page_throttle_rate_limit, period=1.0)(  # type: ignore
                    visit_link
                )
            )
            page = await browser.new_page()
            await visit(page, url, wait_until=wait_until)
            content = await page.content()
            await html.save(content)
    else:
        debug(f"Visiting the URL ({url}) ...")
        visit = with_timeout(async_timeout)(  # type: ignore
            throttle(rate_limit=page_throttle_rate_limit, period=1.0)(  # type: ignore
                visit_link
            )
        )
        page = await browser.new_page()
        await visit(page, url, wait_until=wait_until)

    return page


async def reload_page(
    *,
    browser: Browser,
    url: str,
    html: HTML,
    page_throttle_rate_limit: int = 10,
    async_timeout: int = 600,
    save_html: bool = True,
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "load",
):
    """
    Create a new page in the browser

    If the file exists on disk, then remove it and visit the URL again
    """
    if save_html:
        with suppress(OSError):
            os.remove(html.file)

    debug(f"Visiting the URL ({url}) ...")
    visit = with_timeout(async_timeout)(  # type: ignore
        throttle(rate_limit=page_throttle_rate_limit, period=1.0)(  # type: ignore
            visit_link
        )
    )
    page = await browser.new_page()
    await visit(page, url, wait_until=wait_until)
    content = await page.content()

    if save_html:
        await html.save(content)

    return page


@overload
async def parse_document(
    content: str,
    *,
    engine: Literal["lxml"],
) -> LXMLDocument | None:
    ...


@overload
async def parse_document(
    content: str,
    *,
    engine: Literal["modest"],
) -> ModestDocument | None:
    ...


@overload
async def parse_document(
    content: str,
    *,
    engine: Literal["lexbor"],
) -> LexborDocument | None:
    ...


async def parse_document(
    content: str,
    *,
    engine: Literal["lxml", "modest", "lexbor"] = "lxml",
) -> LXMLDocument | ModestDocument | LexborDocument | None:
    """
    Parse the HTML content using the specified parser ("lxml", "modest", "lexbor")

    Return document object
    """
    if engine == "lxml":
        try:
            tree = cast(lxml.HtmlElement, await asyncio.to_thread(lxml.fromstring, content))  # type: ignore
        except lxml.etree.ParserError:
            return None

        return LXMLDocument(tree)

    elif engine == "lexbor":
        try:
            tree = await asyncio.to_thread(LexborHTMLParser, content)
        except Exception:
            return None

        return LexborDocument(tree)

    elif engine == "modest":
        try:
            tree = await asyncio.to_thread(HTMLParser, content)
        except Exception:
            return None

        return ModestDocument(tree)

    raise ValueError(
        f'Wrong engine type: {engine}\nSupported engines: ["lxml", "modest", "lexbor"]'
    )


@overload
async def parse_document_from_url(
    browser: Browser,
    url: str,
    *,
    page_throttle_rate_limit: int,
    async_timeout: int,
    engine: Literal["lxml"],
) -> LXMLDocument:
    ...


@overload
async def parse_document_from_url(
    browser: Browser,
    url: str,
    *,
    page_throttle_rate_limit: int,
    async_timeout: int,
    engine: Literal["modest"],
) -> ModestDocument:
    ...


@overload
async def parse_document_from_url(
    browser: Browser,
    url: str,
    *,
    page_throttle_rate_limit: int,
    async_timeout: int,
    engine: Literal["lexbor"],
) -> LexborDocument:
    ...


async def parse_document_from_url(
    browser: Browser,
    url: str,
    *,
    page_throttle_rate_limit: int = 10,
    async_timeout: int = 600,
    engine: Literal["lxml", "modest", "lexbor"] = "lxml",
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "load",
) -> LXMLDocument | ModestDocument | LexborDocument:
    """
    Visit the URL and parse the HTML content using the specified parser ("lxml", "modest", "lexbor")

    Return document object if parsing is successful, however, unlike parse_document(), it raises an HTMLParsingError exception if parsing is failed
    """
    page = await browser.new_page()
    visit = with_timeout(async_timeout)(  # type: ignore
        throttle(rate_limit=page_throttle_rate_limit, period=1.0)(  # type: ignore
            visit_link
        )
    )
    await visit(page, url, wait_until=wait_until)
    content = await page.content()
    await page.close()

    if engine == "lxml":
        try:
            tree = cast(
                lxml.HtmlElement,
                await asyncio.to_thread(lxml.fromstring, content),  # type: ignore
            )
        except lxml.etree.ParserError as err:
            raise HTMLParsingError(
                f'Could not parse LXML document due to an error -> "{err}"'
            ) from err

        return LXMLDocument(tree)

    elif engine == "lexbor":
        try:
            tree = await asyncio.to_thread(LexborHTMLParser, content)
        except Exception as err:
            raise HTMLParsingError(
                f'Could not parse LEXXBOR document due to an error -> "{err}"'
            ) from err

        return LexborDocument(tree)

    elif engine == "modest":
        try:
            tree = await asyncio.to_thread(HTMLParser, content)
        except Exception as err:
            raise HTMLParsingError(
                f'Could not parse MODEST document due to an error -> "{err}"'
            ) from err

        return ModestDocument(tree)

    raise ValueError(
        f'Wrong engine type: {engine}\nSupported engines: ["lxml", "modest", "lexbor"]'
    )
