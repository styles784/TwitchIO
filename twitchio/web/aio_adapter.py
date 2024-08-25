"""
MIT License

Copyright (c) 2017 - Present PythonistaGuild

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections import deque
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import unquote_plus

from aiohttp import web

from ..authentication import Scopes
from ..models.eventsub_ import BaseEvent, SubscriptionRevoked
from ..types_.eventsub import EventSubHeaders
from ..utils import _from_json, parse_timestamp  # type: ignore
from .utils import MESSAGE_TYPES, BaseAdapter, verify_message


if TYPE_CHECKING:
    from ..authentication import AuthorizationURLPayload, UserTokenPayload
    from ..client import Client


__all__ = ("AiohttpAdapter",)


logger: logging.Logger = logging.getLogger(__name__)


class AiohttpAdapter(BaseAdapter, web.Application):
    client: Client

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        domain: str | None = None,
        eventsub_path: str | None = None,
        eventsub_secret: str | None = None,
    ) -> None:
        super().__init__()
        self._runner: web.AppRunner | None = None

        self._host: str = host or "localhost"
        self._port: int = port or 4343

        self._eventsub_secret: str | None = eventsub_secret
        if eventsub_secret and not 10 <= len(eventsub_secret) <= 100:
            raise ValueError("Eventsub Secret must be between 10 and 100 characters long.")

        self._domain: str | None = None
        if domain:
            domain_ = domain.removeprefix("http://").removeprefix("https://").removesuffix("/")
            self._domain = f"https://{domain_}"
        else:
            self._domain = f"http://{self._host}:{self._port}"

        path: str = eventsub_path.removeprefix("/").removesuffix("/") if eventsub_path else "callback"
        self._eventsub_path: str = f"/{path}"

        self._runner_task: asyncio.Task[None] | None = None
        self.startup = self.event_startup
        self.shutdown = self.event_shutdown

        self.router.add_route("GET", "/oauth/callback", self.oauth_callback)
        self.router.add_route("GET", "/oauth", self.oauth_redirect)
        self.router.add_route("POST", self._eventsub_path, self.eventsub_callback)

        self._responded: deque[str] = deque(maxlen=5000)

    def __init_subclass__(cls: type[AiohttpAdapter]) -> None:
        return

    @property
    def eventsub_url(self) -> str:
        return f"{self._domain}{self._eventsub_path}"

    @property
    def redirect_url(self) -> str:
        return f"{self._domain}/oauth/callback"

    async def event_startup(self) -> None:
        logger.info("Starting TwitchIO AiohttpAdapter on http://%s:%s.", self._host, self._port)

    async def event_shutdown(self) -> None:
        logger.info("Successfully shutdown TwitchIO <%s>.", self.__class__.__qualname__)

    async def close(self) -> None:
        if self._runner_task is not None:
            try:
                self._runner_task.cancel()
            except Exception as e:
                logger.debug(
                    "Ignoring exception raised while cancelling runner in <%s>: %s.",
                    self.__class__.__qualname__,
                    e,
                )

        if self._runner is not None:
            await self._runner.cleanup()

        self._runner = None
        self._runner_task = None

    async def run(self, host: str | None = None, port: int | None = None) -> None:
        self._runner = web.AppRunner(self, access_log=None, handle_signals=True)
        await self._runner.setup()

        site: web.TCPSite = web.TCPSite(self._runner, host or self._host, port or self._port)
        self._runner_task = asyncio.create_task(site.start(), name=f"twitchio-web-adapter:{self.__class__.__qualname__}")

    async def eventsub_callback(self, request: web.Request) -> web.Response:
        headers: EventSubHeaders = cast(EventSubHeaders, request.headers)
        msg_type: str | None = headers.get("Twitch-Eventsub-Message-Type")

        if not msg_type or msg_type not in MESSAGE_TYPES:
            logger.debug("Eventsub Webhook received an unknown Message-Type header value.")
            return web.Response(status=400)

        if not self._eventsub_secret:
            msg: str = f"Eventsub Webhook '{self!r}' must be passed a secret. See: ... for more info.'"
            return web.Response(text=msg, status=400)

        msg_id: str | None = headers.get("Twitch-Eventsub-Message-Id", None)
        timestamp: str | None = headers.get("Twitch-Eventsub-Message-Timestamp", None)

        if not msg_id or not timestamp:
            return web.Response(text="Bad Request. Invalid Message-ID or Message-Timestamp.", status=400)

        if msg_id in self._responded:
            return web.Response(text="Previously responded to Message.", status=400)

        self._responded.append(msg_id)

        try:
            resp: bytes = await verify_message(request=request, secret=self._eventsub_secret)
        except ValueError:
            return web.Response(text="Challenge Failed. Failed to verify the integrity of the message.", status=400)
        except Exception as e:
            return web.Response(text=f"Challenge Failed. Failed to verify the integrity of the message: {e}", status=400)

        # TODO: Types...
        data: Any = _from_json(resp)  # type: ignore
        sent: datetime.datetime = parse_timestamp(timestamp)
        now: datetime.datetime = datetime.datetime.now(tz=datetime.UTC)

        if sent + datetime.timedelta(minutes=10) <= now:
            return web.Response(text="Message has expired.", status=400)

        if msg_type == "webhook_callback_verification":
            return web.Response(text=data["challenge"], status=200, headers={"Content-Type": "text/plain"})

        elif msg_type == "notification":
            subscription_type: str = data["subscription"]["type"]
            event: str = subscription_type.replace("channel.channel_", "channel.").replace(".", "_")

            try:
                payload_class = BaseEvent.create_instance(subscription_type, data["event"], http=self.client._http)
            except ValueError:
                logger.warning("Webhook '%s' received an unhandled eventsub event: '%s'.", self, event)
                return web.Response(status=200)

            self.client.dispatch(event=event, payload=payload_class)
            return web.Response(status=200)

        elif msg_type == "revocation":
            payload: SubscriptionRevoked = SubscriptionRevoked(data["subscription"])
            self.client.dispatch(event="subscription_revoked", payload=payload)

            return web.Response(status=204)

    async def fetch_token(self, request: web.Request) -> web.Response:
        if "code" not in request.query:
            return web.Response(status=400)

        try:
            payload: UserTokenPayload = await self.client._http.user_access_token(
                request.query["code"],
                redirect_uri=self.redirect_url,
            )
        except Exception as e:
            logger.error("Exception raised while fetching Token in <%s>: %s", self.__class__.__qualname__, e)
            return web.Response(status=500)

        self.client.dispatch(event="oauth_authorized", payload=payload)
        return web.Response(body="Success. You can leave this page.", status=200)

    async def oauth_callback(self, request: web.Request) -> web.Response:
        logger.debug("Received OAuth callback request in <%s>.", self.oauth_callback.__qualname__)

        response: web.Response = await self.fetch_token(request)
        return response

    async def oauth_redirect(self, request: web.Request) -> web.Response:
        scopes: str | None = request.query.get("scopes", None)
        force_verify: bool = request.query.get("force_verify", "false").lower() == "true"

        if not scopes:
            scopes = str(self.client._http.scopes) if self.client._http.scopes else None

        if not scopes:
            logger.warning(
                "No scopes provided in request to <%s>. Scopes are a required parameter that is missing.",
                self.oauth_redirect.__qualname__,
            )
            return web.Response(text="No scopes were provided. Scopes must be provided.", status=400)

        scopes_: Scopes = Scopes(unquote_plus(scopes).split())

        try:
            payload: AuthorizationURLPayload = self.client._http.get_authorization_url(
                scopes=scopes_,
                redirect_uri=self.redirect_url,
                force_verify=force_verify,
            )
        except Exception as e:
            logger.error("Exception raised while fetching Authorization URL in <%s>: %s", self.__class__.__qualname__, e)
            return web.Response(status=500)

        raise web.HTTPPermanentRedirect(payload["url"])
