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

import copy
import datetime
import logging
import sys
import urllib.parse
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Literal, TypeAlias, TypeVar

import aiohttp

from . import __version__
from .conduits import Shard
from .exceptions import HTTPException
from .models.analytics import ExtensionAnalytics, GameAnalytics
from .models.bits import ExtensionTransaction
from .models.channel_points import CustomRewardRedemption
from .models.channels import ChannelFollowerEvent, ChannelFollowers, FollowedChannels, FollowedChannelsEvent
from .models.charity import CharityDonation
from .models.chat import Chatters, UserEmote
from .models.clips import Clip
from .models.games import Game
from .models.hype_train import HypeTrainEvent
from .models.moderation import BannedUser, BlockedTerm, UnbanRequest
from .models.polls import Poll
from .models.predictions import Prediction
from .models.search import SearchChannel
from .models.streams import Stream, VideoMarkers
from .models.subscriptions import BroadcasterSubscription, BroadcasterSubscriptions
from .models.videos import Video
from .user import ActiveExtensions, PartialUser
from .utils import Colour, _from_json  # type: ignore


if TYPE_CHECKING:
    from collections.abc import Generator, Sequence

    from typing_extensions import Self, Unpack

    from .assets import Asset
    from .models.channel_points import CustomReward
    from .models.moderation import AutomodCheckMessage, AutomodSettings
    from .types_.conduits import ShardData, ShardUpdateRequest
    from .types_.requests import APIRequestKwargs, HTTPMethod, ParamMapping
    from .types_.responses import (
        AddBlockedTermResponse,
        AdScheduleResponse,
        AutomodSettingsResponse,
        BannedUsersResponseData,
        BanUserResponse,
        BitsLeaderboardResponse,
        BlockedTermsResponseData,
        BroadcasterSubscriptionsResponseData,
        ChannelChatBadgesResponse,
        ChannelEditorsResponse,
        ChannelEmotesResponse,
        ChannelFollowersResponseData,
        ChannelInformationResponse,
        ChannelTeamsResponse,
        CharityCampaignDonationsResponseData,
        CharityCampaignResponse,
        ChatSettingsResponse,
        ChattersResponseData,
        CheckAutomodStatusResponse,
        CheckUserSubscriptionResponse,
        CheermotesResponse,
        ClipsResponseData,
        ConduitPayload,
        ContentClassificationLabelsResponse,
        CreateClipResponse,
        CreateStreamMarkerResponse,
        CreatorGoalsResponse,
        CustomRewardRedemptionResponse,
        CustomRewardRedemptionResponseData,
        CustomRewardsResponse,
        DeleteVideosResponse,
        EmoteSetsResponse,
        ExtensionAnalyticsResponseData,
        ExtensionTransactionsResponseData,
        FollowedChannelsResponseData,
        GameAnalyticsResponseData,
        GamesResponse,
        GamesResponseData,
        GlobalChatBadgesResponse,
        GlobalEmotesResponse,
        HypeTrainEventsResponseData,
        ModeratedChannelsResponseData,
        ModeratorsResponseData,
        PollsResponse,
        PollsResponseData,
        PredictionsResponse,
        PredictionsResponseData,
        RawResponse,
        ResolveUnbanRequestsResponse,
        SearchChannelsResponseData,
        SendChatMessageResponse,
        ShieldModeStatusResponse,
        SnoozeNextAdResponse,
        StartARaidResponse,
        StartCommercialResponse,
        StreamKeyResponse,
        StreamMarkersResponseData,
        StreamsResponseData,
        TeamsResponse,
        TopGamesResponseData,
        UnbanRequestsResponseData,
        UpdateUserExtensionsResponse,
        UpdateUserResponse,
        UserActiveExtensionsResponse,
        UserBlockListResponseData,
        UserChatColorResponse,
        UserEmotesResponseData,
        UserExtensionsResponse,
        UsersResponse,
        VideosResponseData,
    )


logger: logging.Logger = logging.getLogger(__name__)


T = TypeVar("T")
PaginatedConverter: TypeAlias = Callable[..., Awaitable[T]] | None


async def json_or_text(resp: aiohttp.ClientResponse) -> dict[str, Any] | str:
    text: str = await resp.text()

    try:
        if resp.headers["Content-Type"].startswith("application/json"):
            return _from_json(text)  # type: ignore
    except KeyError:
        pass

    return text


class Route:
    __slots__ = (
        "params",
        "data",
        "json",
        "headers",
        "use_id",
        "method",
        "path",
        "packed",
        "_base_url",
        "_url",
        "token_for",
    )

    BASE: ClassVar[str] = "https://api.twitch.tv/helix/"
    ID_BASE: ClassVar[str] = "https://id.twitch.tv/"

    def __init__(
        self,
        method: HTTPMethod,
        path: str,
        *,
        use_id: bool = False,
        **kwargs: Unpack[APIRequestKwargs],
    ) -> None:
        self.params: ParamMapping = kwargs.pop("params", {})
        self.json: dict[str, Any] = kwargs.get("json", {})
        self.headers: dict[str, str] = kwargs.get("headers", {})
        self.token_for: str = str(kwargs.get("token_for", ""))

        self.use_id = use_id
        self.method = method
        self.path = path

        self._base_url: str = ""
        self._url: str = self.build_url(duplicate_key=not use_id)

    def __str__(self) -> str:
        return str(self._url)

    def __repr__(self) -> str:
        return f"{self.method}[{self.base_url}]"

    def build_url(self, *, remove_none: bool = True, duplicate_key: bool = True) -> str:
        base = self.ID_BASE if self.use_id else self.BASE
        self.path = self.path.lstrip("/").rstrip("/")

        url: str = f"{base}{self.path}"
        self._base_url = url

        if not self.params:
            return url

        url += "?"

        # We expect a dict so keys should be unique...
        for key, value in copy.copy(self.params).items():
            if value is None:
                if remove_none:
                    del self.params[key]
                continue

            if isinstance(value, (str, int)):
                url += f'{key}={self.encode(str(value), safe="+", plus=True)}&'
            elif duplicate_key:
                for v in value:
                    url += f"{key}={self.encode(str(v), safe='+', plus=True)}&"
            else:
                joined: str = "+".join([self.encode(str(v), safe="+") for v in value])
                url += f"{key}={joined}&"

        return url.rstrip("&")

    @classmethod
    def encode(cls, value: str, /, safe: str = "", plus: bool = False) -> str:
        method = urllib.parse.quote_plus if plus else urllib.parse.quote
        unquote = urllib.parse.unquote_plus if plus else urllib.parse.unquote

        return method(value, safe=safe) if unquote(value) == value else value

    @property
    def url(self) -> str:
        return self._url

    @property
    def base_url(self) -> str:
        return self._base_url

    def update_params(self, params: ParamMapping, *, remove_none: bool = True) -> str:
        self.params.update(params)
        self._url = self.build_url(remove_none=remove_none)

        return self.url

    def update_headers(self, headers: dict[str, str]) -> None:
        self.headers.update(headers)


class HTTPAsyncIterator(Generic[T]):
    __slots__ = (
        "_http",
        "_route",
        "_cursor",
        "_first",
        "_max_results",
        "_converter",
        "_buffer",
    )

    def __init__(
        self,
        http: HTTPClient,
        route: Route,
        max_results: int | None = None,
        converter: PaginatedConverter[T] = None,
    ) -> None:
        self._http = http
        self._route = route

        self._cursor: str | None | bool = None
        self._first: int = int(route.params.get("first", 20))  # 20 is twitch default
        self._max_results: int | None = max_results

        if self._max_results is not None and self._max_results < self._first:
            self._first = self._max_results

        self._converter = converter or self._base_converter
        self._buffer: deque[T] = deque()

    async def _base_converter(self, data: Any, *, raw: Any = None) -> T:
        if raw is None:
            raw = {}
        return data

    async def _call_next(self) -> None:
        if self._cursor is False:
            raise StopAsyncIteration

        if self._max_results is not None and self._max_results <= 0:
            raise StopAsyncIteration

        self._route.update_params({"after": self._cursor})
        data: RawResponse = await self._http.request_json(self._route)
        self._cursor = data.get("pagination", {}).get("cursor", False)

        try:
            inner: list[RawResponse] = data["data"]
        except KeyError as e:
            # TODO: Proper exception...
            raise ValueError('Expected "data" key not found.') from e

        for value in inner:
            if self._max_results is None:
                self._buffer.append(await self._do_conversion(value, raw=data))
                continue

            self._max_results -= 1  # If this is causing issues, it's just pylance bugged/desynced...
            if self._max_results < 0:
                return

            self._buffer.append(await self._do_conversion(value, raw=data))

    async def _do_conversion(self, data: RawResponse, *, raw: RawResponse) -> T:
        return await self._converter(data, raw=raw)

    async def _flatten(self) -> list[T]:
        if not self._buffer:
            await self._call_next()

        return list(self._buffer)

    def __await__(self) -> Generator[Any, None, list[T]]:
        return self._flatten().__await__()

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> T:
        if not self._buffer:
            await self._call_next()

        try:
            data = self._buffer.popleft()
        except IndexError as e:
            raise StopAsyncIteration from e

        return data


class HTTPClient:
    __slots__ = ("_session", "_client_id", "user_agent")

    def __init__(self, session: aiohttp.ClientSession | None = None, *, client_id: str) -> None:
        self._session: aiohttp.ClientSession | None = session  # should be set on the first request
        self._client_id: str = client_id

        # User Agent...
        pyver = f"{sys.version_info[0]}.{sys.version_info[1]}"
        ua = "TwitchioClient (https://github.com/PythonistaGuild/TwitchIO {0}) Python/{1} aiohttp/{2}"
        self.user_agent: str = ua.format(__version__, pyver, aiohttp.__version__)

    @property
    def headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent, "Client-ID": self._client_id}

    async def _init_session(self) -> None:
        if self._session and not self._session.closed:
            return

        logger.debug("Initialising a new session on %s.", self.__class__.__qualname__)

        session = self._session or aiohttp.ClientSession()
        session.headers.update(self.headers)

        self._session = session

    def clear(self) -> None:
        if self._session and self._session.closed:
            logger.debug(
                "Clearing %s session. A new session will be created on the next request.",
                self.__class__.__qualname__,
            )
            self._session = None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception as e:
                logger.debug(
                    "Ignoring exception caught while closing %s session: %s.",
                    self.__class__.__qualname__,
                    e,
                )

            self.clear()
            logger.debug("%s session closed successfully.", self.__class__.__qualname__)

    async def request(self, route: Route) -> RawResponse | str | None:
        await self._init_session()
        assert self._session is not None

        logger.debug("Attempting a request to %r with %s.", route, self.__class__.__qualname__)

        async with self._session.request(
            route.method,
            route.url,
            headers=route.headers,
            json=route.json or None,
        ) as resp:
            data: RawResponse | str = await json_or_text(resp)

            if resp.status >= 400:
                raise HTTPException(
                    f"Request {route} failed with status {resp.status}: {data}",
                    route=route,
                    status=resp.status,
                    extra=data,
                )

            if resp.status == 204:
                return None

        return data

    async def request_json(self, route: Route) -> Any:
        data = await self.request(route)

        if isinstance(data, str):
            # TODO: Add a HTTPException here.
            raise TypeError("Expected JSON data, but received text data.")

        return data

    async def _request_asset_head(self, url: str) -> dict[str, str]:
        await self._init_session()
        assert self._session is not None

        logger.debug('Attempting to request headers for asset "%s" with %s.', url, self.__class__.__qualname__)

        async with self._session.head(url) as resp:
            if resp.status != 200:
                msg = f'Failed to header for asset at "{url}" with status {resp.status}.'
                raise HTTPException(msg, status=resp.status, extra=await resp.text())

            return dict(resp.headers)

    async def _request_asset(self, asset: Asset, *, chunk_size: int = 1024) -> AsyncIterator[bytes]:
        await self._init_session()
        assert self._session is not None

        logger.debug('Attempting a request to asset "%r" with %s.', asset, self.__class__.__qualname__)

        async with self._session.get(asset.url) as resp:
            if resp.status != 200:
                msg = f'Failed to get asset at "{asset.url}" with status {resp.status}.'
                raise HTTPException(msg, status=resp.status, extra=await resp.text())

            headers: dict[str, str] = dict(resp.headers)
            asset._set_ext(headers)

            async for chunk in resp.content.iter_chunked(chunk_size):
                yield chunk

    def request_paginated(
        self,
        route: Route,
        max_results: int | None = None,
        *,
        converter: PaginatedConverter[T] | None = None,
    ) -> HTTPAsyncIterator[T]:
        iterator: HTTPAsyncIterator[T] = HTTPAsyncIterator(self, route, max_results, converter=converter)
        return iterator

    ### Ads ###

    async def start_commercial(self, broadcaster_id: str | int, length: int, token_for: str) -> StartCommercialResponse:
        data = {"broadcaster_id": broadcaster_id, "length": length}

        route: Route = Route("POST", "channels/commercial", json=data, token_for=token_for)
        return await self.request_json(route)

    async def get_ad_schedule(self, broadcaster_id: str | int, token_for: str) -> AdScheduleResponse:
        params = {"broadcaster_id": broadcaster_id}

        route: Route = Route("GET", "channels/ads", params=params, token_for=token_for)
        return await self.request_json(route)

    async def post_snooze_ad(self, broadcaster_id: str | int, token_for: str) -> SnoozeNextAdResponse:
        params = {"broadcaster_id": broadcaster_id}

        route: Route = Route("POST", "channels/ads/snooze", params=params, token_for=token_for)
        return await self.request_json(route)

    ### Analytics ###

    def get_extension_analytics(
        self,
        *,
        token_for: str,
        extension_id: str | None = None,
        type: Literal["overview_v2"] = "overview_v2",
        started_at: datetime.date | None = None,
        ended_at: datetime.date | None = None,
        first: int = 20,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[ExtensionAnalytics]:
        params = {"type": type, "First": first}

        if extension_id:
            params["extension_id"] = extension_id

        def date_to_datetime_with_z(date: datetime.date) -> str:
            return datetime.datetime.combine(date, datetime.time(0, 0)).isoformat() + "Z"

        if started_at and ended_at:
            params["started_at"] = date_to_datetime_with_z(started_at)
            params["ended_at"] = date_to_datetime_with_z(ended_at)

        route: Route = Route("GET", "analytics/extensions", params=params, token_for=token_for)

        async def converter(data: ExtensionAnalyticsResponseData, *, raw: Any) -> ExtensionAnalytics:
            return ExtensionAnalytics(data)

        iterator = self.request_paginated(route, converter=converter, max_results=max_results)
        return iterator

    def get_game_analytics(
        self,
        *,
        token_for: str,
        game_id: str | None = None,
        type: Literal["overview_v2"] = "overview_v2",
        started_at: datetime.date | None = None,
        ended_at: datetime.date | None = None,
        first: int = 20,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[GameAnalytics]:
        params = {"type": type, "First": first}

        if game_id:
            params["game_id"] = game_id

        def date_to_datetime_with_z(date: datetime.date) -> str:
            return datetime.datetime.combine(date, datetime.time(0, 0)).isoformat() + "Z"

        if started_at and ended_at:
            params["started_at"] = date_to_datetime_with_z(started_at)
            params["ended_at"] = date_to_datetime_with_z(ended_at)

        route: Route = Route("GET", "analytics/games", params=params, token_for=token_for)

        async def converter(data: GameAnalyticsResponseData, *, raw: Any) -> GameAnalytics:
            return GameAnalytics(data)

        iterator = self.request_paginated(route, converter=converter, max_results=max_results)
        return iterator

    ### Bits ###

    async def get_bits_leaderboard(
        self,
        *,
        broadcaster_id: str | int,
        token_for: str,
        count: int = 10,
        period: Literal["day", "week", "month", "year", "all"] = "all",
        started_at: datetime.datetime | None = None,
        user_id: str | int | None = None,
    ) -> BitsLeaderboardResponse:
        params: dict[str, str | int | datetime.datetime] = {
            "broadcaster_id": broadcaster_id,
            "count": count,
            "period": period,
        }

        if started_at is not None:
            params["started_at"] = started_at
        if user_id:
            params["user_id"] = user_id

        route: Route = Route("GET", "bits/leaderboard", params=params, token_for=token_for)
        return await self.request_json(route)

    async def get_cheermotes(
        self,
        broadcaster_id: str | int | None = None,
        token_for: str | None = None,
    ) -> CheermotesResponse:
        params = {"broadcaster_id": broadcaster_id} if broadcaster_id is not None else {}

        route: Route = Route("GET", "bits/cheermotes", params=params, token_for=token_for)
        return await self.request_json(route)

    def get_extension_transactions(
        self,
        *,
        extension_id: str,
        ids: list[str] | None = None,
        first: int = 20,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[ExtensionTransaction]:
        params: dict[str, str | int | list[str]] = {"extension_id": extension_id, "first": first}
        if ids:
            params["id"] = ids

        route: Route = Route("GET", "extensions/transactions", params=params)

        async def converter(data: ExtensionTransactionsResponseData, *, raw: Any) -> ExtensionTransaction:
            return ExtensionTransaction(data, http=self)

        iterator: HTTPAsyncIterator[ExtensionTransaction] = self.request_paginated(
            route, converter=converter, max_results=max_results
        )

        return iterator

    ### Channels ###

    async def get_channel_info(
        self,
        broadcaster_ids: list[str | int],
        token_for: str | None = None,
    ) -> ChannelInformationResponse:
        params = {"broadcaster_id": broadcaster_ids}

        route: Route = Route("GET", "channels", params=params, token_for=token_for)
        return await self.request_json(route)

    async def patch_channel_info(
        self,
        *,
        broadcaster_id: str | int,
        token_for: str,
        game_id: str | int | None = None,
        language: str | None = None,
        title: str | None = None,
        delay: int | None = None,
        tags: list[str] | None = None,
        branded_content: bool | None = None,
        classification_labels: list[
            dict[Literal["DrugsIntoxication", "SexualThemes", "ViolentGraphic", "Gambling", "ProfanityVulgarity"], bool]
        ]
        | None = None,
    ) -> None:  # TODO: Returns None?
        params = {"broadcaster_id": broadcaster_id}

        data: dict[str, str | int | list[str] | list[dict[str, str | bool]]] = {
            k: v
            for k, v in {
                "game_id": game_id,
                "broadcaster_language": language,
                "title": title,
                "delay": delay,
                "tags": tags,
                "is_branded_content": branded_content,
            }.items()
            if v is not None
        }

        if classification_labels is not None:
            converted_labels = [
                {"id": label, "is_enabled": enabled}
                for item in classification_labels
                for label, enabled in item.items()
            ]
            data["content_classification_labels"] = converted_labels

        route: Route = Route("PATCH", "channels", params=params, json=data, token_for=token_for)
        return await self.request_json(route)

    async def get_channel_editors(self, broadcaster_id: str | int, token_for: str) -> ChannelEditorsResponse:
        params = {"broadcaster_id": broadcaster_id}

        route: Route = Route("GET", "channels/editors", params=params, token_for=token_for)
        return await self.request_json(route)

    async def get_followed_channels(
        self,
        *,
        user_id: str | int,
        token_for: str,
        broadcaster_id: str | int | None = None,
        first: int = 20,
    ) -> FollowedChannels:
        params = {"first": first, "user_id": user_id}

        if broadcaster_id is not None:
            params["broadcaster_id"] = broadcaster_id

        route = Route("GET", "channels/followed", params=params, token_for=token_for)

        async def converter(data: FollowedChannelsResponseData, *, raw: Any) -> FollowedChannelsEvent:
            return FollowedChannelsEvent(data, http=self)

        iterator = self.request_paginated(route, converter=converter)
        data = await self.request_json(route)

        return FollowedChannels(data, iterator)

    async def get_channel_followers(
        self,
        *,
        broadcaster_id: str | int,
        token_for: str,
        user_id: str | int | None = None,
        first: int = 20,
    ) -> ChannelFollowers:
        params = {"first": first, "broadcaster_id": broadcaster_id}

        if user_id is not None:
            params["user_id"] = broadcaster_id

        route = Route("GET", "channels/followers", params=params, token_for=token_for)

        async def converter(data: ChannelFollowersResponseData) -> ChannelFollowerEvent:
            return ChannelFollowerEvent(data, http=self)

        iterator = self.request_paginated(route, converter=converter)
        data = await self.request_json(route)

        return ChannelFollowers(data, iterator)

    ### Channel Points ###

    async def post_custom_reward(
        self,
        *,
        broadcaster_id: str,
        token_for: str,
        title: str,
        cost: int,
        prompt: str | None = None,
        enabled: bool = True,
        background_color: str | Colour | None = None,
        max_per_stream: int | None = None,
        max_per_user: int | None = None,
        global_cooldown: int | None = None,
        skip_queue: bool = False,
    ) -> CustomRewardsResponse:
        params = {"broadcaster_id": broadcaster_id}
        data = {
            "title": title,
            "cost": cost,
            "is_enabled": enabled,
            "should_redemptions_skip_request_queue": skip_queue,
        }

        if prompt is not None:
            data["prompt"] = prompt
            data["is_user_input_required"] = True

        if background_color:
            if isinstance(background_color, Colour):
                background_color = str(background_color)
            data["background_color"] = background_color

        if max_per_stream:
            data["max_per_stream"] = max_per_stream
            data["is_max_per_stream_enabled"] = True

        if max_per_user:
            data["max_per_user_per_stream"] = max_per_user
            data["is_max_per_user_per_stream_enabled"] = True

        if global_cooldown:
            data["global_cooldown_seconds"] = global_cooldown
            data["is_global_cooldown_enabled"] = True

        route: Route = Route("POST", "channel_points/custom_rewards", params=params, json=data, token_for=token_for)
        return await self.request_json(route)

    async def delete_custom_reward(self, broadcaster_id: str, reward_id: str, token_for: str) -> None:
        params = {"broadcaster_id": broadcaster_id, "id": reward_id}

        route: Route = Route("DELETE", "channel_points/custom_rewards", params=params, token_for=token_for)
        return await self.request_json(route)

    async def get_custom_reward(
        self,
        *,
        broadcaster_id: str,
        token_for: str,
        reward_ids: list[str] | None = None,
        manageable: bool = False,
    ) -> CustomRewardsResponse:
        params: dict[str, str | bool | list[str]] = {
            "broadcaster_id": broadcaster_id,
            "only_manageable_rewards": manageable,
        }

        if reward_ids is not None:
            params["id"] = reward_ids

        route: Route = Route("GET", "channel_points/custom_rewards", params=params, token_for=token_for)
        return await self.request_json(route)

    async def patch_custom_reward(
        self,
        *,
        broadcaster_id: str,
        token_for: str,
        reward_id: str,
        title: str | None = None,
        cost: int | None = None,
        prompt: str | None = None,
        enabled: bool | None = None,
        background_color: str | Colour | None = None,
        user_input_required: bool | None = None,
        max_per_stream: int | None = None,
        max_per_user: int | None = None,
        global_cooldown: int | None = None,
        skip_queue: bool | None = None,
    ) -> CustomRewardsResponse:
        params = {
            "broadcaster_id": broadcaster_id,
            "id": reward_id,
        }

        data: dict[str, str | int | bool] = {}

        if title is not None:
            data["title"] = title

        if cost is not None:
            data["cost"] = cost

        if prompt is not None:
            data["prompt"] = prompt
            data["user_input_required"] = True

        if enabled is not None:
            data["is_enabled"] = enabled

        if background_color:
            if isinstance(background_color, Colour):
                background_color = str(background_color)
            data["background_color"] = background_color

        if user_input_required is not None:
            data["is_user_input_required"] = user_input_required

        if skip_queue is not None:
            data["should_redemptions_skip_request_queue"] = skip_queue

        if max_per_stream is not None:
            data["max_per_stream"] = max_per_stream
            data["is_max_per_stream_enabled"] = max_per_stream != 0

        if max_per_user is not None:
            data["max_per_user_per_stream"] = max_per_user
            data["is_max_per_user_per_stream_enabled"] = max_per_user != 0

        if global_cooldown is not None:
            data["global_cooldown_seconds"] = global_cooldown
            data["is_global_cooldown_enabled"] = global_cooldown != 0

        route: Route = Route("PATCH", "channel_points/custom_rewards", params=params, json=data, token_for=token_for)
        return await self.request_json(route)

    def get_custom_reward_redemptions(
        self,
        *,
        broadcaster_id: str,
        token_for: str,
        reward_id: str,
        parent_reward: CustomReward,
        status: Literal["CANCELED", "FULFILLED", "UNFULFILLED"] | None = None,
        ids: list[str] | None = None,
        sort: Literal["OLDEST", "NEWEST"] = "OLDEST",
        first: int = 20,
    ) -> HTTPAsyncIterator[CustomRewardRedemption]:
        params: dict[str, str | int | list[str]] = {
            "broadcaster_id": broadcaster_id,
            "reward_id": reward_id,
            "sort": sort,
            "First": first,
        }
        if ids is None and status is None:
            raise ValueError("You must provide at least a status if not providing any ids.")

        if ids is not None:
            params["id"] = ids
        if status is not None:
            params["status"] = status

        route: Route = Route("GET", "channel_points/custom_rewards/redemptions", params=params, token_for=token_for)

        async def converter(data: CustomRewardRedemptionResponseData, *, raw: Any) -> CustomRewardRedemption:
            return CustomRewardRedemption(data, parent_reward=parent_reward, http=self)

        iterator = self.request_paginated(route, converter=converter)
        return iterator

    async def patch_custom_reward_redemption(
        self,
        *,
        broadcaster_id: str,
        token_for: str,
        reward_id: str,
        id: str,
        status: Literal["CANCELED", "FULFILLED"],
    ) -> CustomRewardRedemptionResponse:
        params = {"broadcaster_id": broadcaster_id, "reward_id": reward_id, "id": id}
        data = {"status": status}

        route: Route = Route(
            "PATCH",
            "channel_points/custom_rewards/redemptions",
            params=params,
            json=data,
            token_for=token_for,
        )

        return await self.request_json(route)

    ### Charity ###

    async def get_charity_campaign(self, broadcaster_id: str, token_for: str) -> CharityCampaignResponse:
        params = {"broadcaster_id": broadcaster_id}

        route: Route = Route("GET", "charity/campaigns", params=params, token_for=token_for)
        return await self.request_json(route)

    def get_charity_donations(
        self,
        broadcaster_id: str,
        token_for: str,
        first: int = 20,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[CharityDonation]:
        params = {"broadcaster_id": broadcaster_id, "first": first}
        route: Route = Route("GET", "charity/donations", params=params, token_for=token_for)

        async def converter(data: CharityCampaignDonationsResponseData, *, raw: Any) -> CharityDonation:
            return CharityDonation(data, http=self)

        iterator = self.request_paginated(route, converter=converter, max_results=max_results)
        return iterator

    ### Chat ###

    async def get_chatters(
        self,
        token_for: str,
        broadcaster_id: str | int,
        moderator_id: str | int,
        first: int = 100,
        max_results: int | None = None,
    ) -> Chatters:
        params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id, "first": first}
        route: Route = Route("GET", "chat/chatters", params=params, token_for=token_for)

        async def converter(data: ChattersResponseData) -> PartialUser:
            return PartialUser(data["user_id"], data["user_login"], http=self)

        iterator = self.request_paginated(route, converter=converter, max_results=max_results)
        data = await self.request_json(route)

        return Chatters(iterator, data)

    async def get_global_chat_badges(self, token_for: str | None = None) -> GlobalChatBadgesResponse:
        route: Route = Route("GET", "chat/badges/global", token_for=token_for)
        return await self.request_json(route)

    async def get_user_chat_color(
        self,
        user_ids: list[str | int],
        token_for: str | None = None,
    ) -> UserChatColorResponse:
        params: dict[str, list[str | int]] = {"user_id": user_ids}

        route: Route = Route("GET", "chat/color", params=params, token_for=token_for)
        return await self.request_json(route)

    async def get_channel_emotes(
        self,
        broadcaster_id: str | int,
        token_for: str | None = None,
    ) -> ChannelEmotesResponse:
        params = {"broadcaster_id": broadcaster_id}

        route: Route = Route("GET", "chat/emotes", params=params, token_for=token_for)
        return await self.request_json(route)

    async def get_global_emotes(self, token_for: str | None = None) -> GlobalEmotesResponse:
        route: Route = Route("GET", "chat/emotes/global", token_for=token_for)
        return await self.request_json(route)

    async def get_emote_sets(self, emote_set_ids: list[str], token_for: str | None = None) -> EmoteSetsResponse:
        params = {"emote_set_id": emote_set_ids}

        route: Route = Route("GET", "chat/emotes/set", params=params, token_for=token_for)
        return await self.request_json(route)

    async def get_channel_chat_badges(
        self,
        broadcaster_id: str,
        token_for: str | None = None,
    ) -> ChannelChatBadgesResponse:
        params = {"broadcaster_id": broadcaster_id}

        route: Route = Route("GET", "chat/badges", params=params, token_for=token_for)
        return await self.request_json(route)

    async def get_channel_chat_settings(
        self,
        broadcaster_id: str,
        moderator_id: str | int | None = None,
        token_for: str | None = None,
    ) -> ChatSettingsResponse:
        params = {"broadcaster_id": broadcaster_id}
        if moderator_id is not None:
            params["moderator_id"] = str(moderator_id)

        route: Route = Route("GET", "chat/settings", params=params, token_for=token_for)
        return await self.request_json(route)

    def get_user_emotes(
        self,
        user_id: str,
        token_for: str,
        broadcaster_id: str | int | None = None,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[UserEmote]:
        params = {"user_id": user_id}
        if broadcaster_id is not None:
            params["broadcaster_id"] = str(broadcaster_id)

        route: Route = Route("GET", "chat/emotes/user", params=params, token_for=token_for)

        async def converter(data: UserEmotesResponseData, *, raw: Any) -> UserEmote:
            return UserEmote(data, template=raw["template"], http=self)

        iterator = self.request_paginated(route, converter=converter, max_results=max_results)
        return iterator

    async def patch_chat_settings(
        self,
        broadcaster_id: str | int,
        moderator_id: str | int,
        token_for: str,
        emote_mode: bool | None = None,
        follower_mode: bool | None = None,
        follower_mode_duration: int | None = None,
        slow_mode: bool | None = None,
        slow_mode_wait_time: int | None = None,
        subscriber_mode: bool | None = None,
        unique_chat_mode: bool | None = None,
        non_moderator_chat_delay: bool | None = None,
        non_moderator_chat_delay_duration: Literal[2, 4, 6] | None = None,
    ) -> ChatSettingsResponse:
        params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}

        _data = {
            "emote_mode": emote_mode,
            "follower_mode": follower_mode,
            "follower_mode_duration": follower_mode_duration,
            "slow_mode": slow_mode,
            "slow_mode_wait_time": slow_mode_wait_time,
            "subscriber_mode": subscriber_mode,
            "unique_chat_mode": unique_chat_mode,
            "non_moderator_chat_delay": non_moderator_chat_delay,
            "non_moderator_chat_delay_duration": non_moderator_chat_delay_duration,
        }
        data = {k: v for k, v in _data.items() if v is not None}

        route: Route = Route("PATCH", "chat/settings", params=params, json=data, token_for=token_for)
        return await self.request_json(route)

    async def post_chat_announcement(
        self,
        broadcaster_id: str | int,
        moderator_id: str | int,
        token_for: str,
        message: str,
        color: Literal["blue", "green", "orange", "purple", "primary"] = "primary",
    ) -> None:
        params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}
        data = {"color": color, "message": message}

        route: Route = Route("POST", "chat/announcements", json=data, params=params, token_for=token_for)
        return await self.request_json(route)

    async def post_chat_shoutout(
        self,
        broadcaster_id: str | int,
        to_broadcaster_id: str | int,
        moderator_id: str | int,
        token_for: str,
    ) -> None:
        params = {
            "broadcaster_id": broadcaster_id,
            "moderator_id": moderator_id,
            "to_broadcaster_id": to_broadcaster_id,
        }

        route: Route = Route("POST", "chat/shoutouts", params=params, token_for=token_for)
        return await self.request_json(route)

    async def post_chat_message(
        self,
        broadcaster_id: str,
        sender_id: str | int,
        message: str,
        token_for: str,
        reply_to_message_id: str | None = None,
    ) -> SendChatMessageResponse:
        data = {"broadcaster_id": broadcaster_id, "sender_id": sender_id, "message": message}
        if reply_to_message_id is not None:
            data["reply_parent_message_id"] = reply_to_message_id

        route: Route = Route("POST", "chat/messages", json=data, token_for=token_for)
        return await self.request_json(route)

    async def put_user_chat_color(self, user_id: str | int, color: str, token_for: str) -> None:
        params = {"user_id": user_id, "color": color}

        route: Route = Route("PUT", "chat/color", params=params, token_for=token_for)
        return await self.request_json(route)

    ### Clips ###

    def get_clips(
        self,
        *,
        first: int,
        broadcaster_id: str | None = None,
        game_id: str | None = None,
        clip_ids: list[str] | None = None,
        started_at: datetime.datetime | None = None,
        ended_at: datetime.datetime | None = None,
        is_featured: bool | None = None,
        token_for: str | None = None,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[Clip]:
        params: dict[str, str | int | list[str]] = {"first": first}

        if broadcaster_id:
            params["broadcaster_id"] = broadcaster_id
        elif game_id:
            params["game_id"] = game_id
        elif clip_ids:
            params["id"] = clip_ids

        if started_at:
            params["started_at"] = started_at.isoformat()
        if ended_at:
            params["ended_at"] = ended_at.isoformat()
        if is_featured is not None:
            params["is_featured"] = is_featured

        route: Route = Route("GET", "clips", params=params, token_for=token_for)

        async def converter(data: ClipsResponseData, *, raw: Any) -> Clip:
            return Clip(data, http=self)

        iterator: HTTPAsyncIterator[Clip] = self.request_paginated(route, converter=converter, max_results=max_results)
        return iterator

    async def post_create_clip(
        self,
        *,
        broadcaster_id: str | int,
        token_for: str,
        has_delay: bool = False,
    ) -> CreateClipResponse:
        params = {"broadcaster_id": broadcaster_id, "has_delay": has_delay}

        route: Route = Route("POST", "clips", params=params, token_for=token_for)
        return await self.request_json(route)

    ### Conduits ###

    async def update_conduit_shards(self, conduit_id: str, /, *, shards: list[ShardUpdateRequest]) -> ...:
        # TODO: Type for return...

        params = {"conduit_id": conduit_id}
        body = {"shards": shards}

        route = Route("PATCH", "eventsub/conduits/shards", params=params, json=body)
        return await self.request_json(route)

    async def create_conduit(self, shard_count: int, /) -> ConduitPayload:
        params = {"shard_count": shard_count}

        route: Route = Route("POST", "eventsub/conduits", params=params)
        return await self.request_json(route)

    async def get_conduits(self) -> ConduitPayload:
        route = Route("GET", "eventsub/conduits")
        return await self.request_json(route)

    def get_conduit_shards(self, conduit_id: str, /, *, status: str | None = None) -> HTTPAsyncIterator[Shard]:
        params = {"conduit_id": conduit_id}
        if status:
            params["status"] = status

        async def converter(data: ShardData, *, raw: Any) -> Shard:
            return Shard(data=data)

        route: Route = Route("GET", "eventsub/conduits/shards", params=params)
        iterator = self.request_paginated(route, converter=converter)

        return iterator

    async def update_conduits(self, id: str, /, shard_count: int) -> ConduitPayload:
        params = {"id": id, "shard_count": shard_count}

        route: Route = Route("PATCH", "eventsub/conduits", params=params)
        return await self.request_json(route)

    ### CCLs ###

    async def get_content_classification_labels(
        self,
        locale: str,
        token_for: str | None = None,
    ) -> ContentClassificationLabelsResponse:
        params: dict[str, str] = {"locale": locale}

        route: Route = Route("GET", "content_classification_labels", params=params, token_for=token_for)
        return await self.request_json(route)

    ### Entitlements ###

    ### Extensions ###

    ### EventSub ###

    ### Games ###

    def get_top_games(
        self,
        first: int,
        token_for: str | None = None,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[Game]:
        params: dict[str, int] = {"first": first}

        route: Route = Route("GET", "games/top", params=params, token_for=token_for)

        async def converter(data: TopGamesResponseData, *, raw: Any) -> Game:
            return Game(data, http=self)

        iterator: HTTPAsyncIterator[Game] = self.request_paginated(route, converter=converter, max_results=max_results)
        return iterator

    async def get_games(
        self,
        *,
        names: list[str] | None = None,
        ids: list[str] | None = None,
        igdb_ids: list[str] | None = None,
        token_for: str | None = None,
    ) -> GamesResponse:
        params: dict[str, list[str]] = {}

        if names is not None:
            params["name"] = names
        if ids is not None:
            params["id"] = ids
        if igdb_ids is not None:
            params["igdb_id"] = igdb_ids

        route: Route = Route("GET", "games", params=params, token_for=token_for)
        return await self.request_json(route)

    ### Goals ###

    async def get_creator_goals(self, broadcaster_id: str | int, token_for: str) -> CreatorGoalsResponse:
        params = {"broadcaster_id": broadcaster_id}

        route: Route = Route("GET", "goals", params=params, token_for=token_for)
        return await self.request_json(route)

    ### Guest Start ###

    ### Hype Train ###

    def get_hype_train_events(
        self,
        broadcaster_id: str | int,
        token_for: str,
        first: int = 1,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[HypeTrainEvent]:
        params = {"broadcaster_id": broadcaster_id, "first": first}

        route: Route = Route("GET", "hypetrain/events", params=params, token_for=token_for)

        async def converter(data: HypeTrainEventsResponseData, *, raw: Any) -> HypeTrainEvent:
            return HypeTrainEvent(data, http=self)

        iterator: HTTPAsyncIterator[HypeTrainEvent] = self.request_paginated(
            route, converter=converter, max_results=max_results
        )

        return iterator

    ### Moderation ###

    async def post_check_automod_status(
        self,
        broadcaster_id: str | int,
        messages: list[AutomodCheckMessage],
        token_for: str,
    ) -> CheckAutomodStatusResponse:
        params = {"broadcaster_id": broadcaster_id}
        msg = [x._to_dict() for x in messages]
        data = {"data": msg}

        route: Route = Route("POST", "moderation/enforcements/status", params=params, json=data, token_for=token_for)
        return await self.request_json(route)

    async def post_manage_automod_messages(
        self,
        user_id: str | int,
        msg_id: str,
        action: Literal["ALLOW", "DENY"],
        token_for: str,
    ) -> None:
        data = {"user_id": user_id, "msg_id": msg_id, "action": action}

        route: Route = Route("POST", "moderation/automod/message", json=data, token_for=token_for)
        return await self.request_json(route)

    async def get_automod_settings(
        self,
        broadcaster_id: str | int,
        moderator_id: str | int,
        token_for: str,
    ) -> AutomodSettingsResponse:
        params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}

        route: Route = Route("GET", "moderation/automod/settings", params=params, token_for=token_for)
        return await self.request_json(route)

    async def put_automod_settings(
        self,
        broadcaster_id: str | int,
        moderator_id: str | int,
        settings: AutomodSettings,
        token_for: str,
    ) -> AutomodSettingsResponse:
        params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}
        data = settings.to_dict()

        route: Route = Route("PUT", "moderation/automod/settings", params=params, json=data, token_for=token_for)
        return await self.request_json(route)

    def get_banned_users(
        self,
        broadcaster_id: str | int,
        token_for: str,
        user_ids: list[str | int] | None = None,
        first: int = 20,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[BannedUser]:
        params: dict[str, str | int | list[str | int]] = {"broadcaster_id": broadcaster_id, "first": first}
        if user_ids is not None:
            params["user_id"] = user_ids

        route: Route = Route("GET", "moderation/banned", params=params, token_for=token_for)

        async def converter(data: BannedUsersResponseData, *, raw: Any) -> BannedUser:
            return BannedUser(data, http=self)

        iterator: HTTPAsyncIterator[BannedUser] = self.request_paginated(
            route, converter=converter, max_results=max_results
        )

        return iterator

    async def post_ban_user(
        self,
        broadcaster_id: str | int,
        moderator_id: str | int,
        token_for: str,
        user_id: str | int,
        duration: int | None = None,
        reason: str | None = None,
    ) -> BanUserResponse:
        params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}
        data = {"user_id": user_id}

        if duration is not None:
            data["duration"] = duration
        if reason is not None:
            data["reason"] = reason

        route: Route = Route("POST", "moderation/bans", params=params, json=data, token_for=token_for)
        return await self.request_json(route)

    async def delete_unban_user(
        self,
        broadcaster_id: str | int,
        moderator_id: str | int,
        token_for: str,
        user_id: str | int,
    ) -> None:
        params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id, "user_id": user_id}

        route: Route = Route("DELETE", "moderation/bans", params=params, token_for=token_for)
        return await self.request_json(route)

    def get_unban_requests(
        self,
        broadcaster_id: str | int,
        moderator_id: str | int,
        token_for: str,
        status: Literal["pending", "approved", "denied", "acknowledged", "canceled"],
        user_id: str | int | None = None,
        first: int = 20,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[UnbanRequest]:
        params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id, "status": status, "first": first}
        if user_id is not None:
            params["user_id"] = user_id

        route: Route = Route("GET", "moderation/unban_requests", params=params, token_for=token_for)

        async def converter(data: UnbanRequestsResponseData, *, raw: Any) -> UnbanRequest:
            return UnbanRequest(data, http=self)

        iterator: HTTPAsyncIterator[UnbanRequest] = self.request_paginated(
            route, converter=converter, max_results=max_results
        )

        return iterator

    async def patch_unban_requests(
        self,
        broadcaster_id: str | int,
        moderator_id: str | int,
        token_for: str,
        status: Literal["approved", "denied"],
        unban_request_id: str,
        resolution_text: str | None = None,
    ) -> ResolveUnbanRequestsResponse:
        params = {
            "broadcaster_id": broadcaster_id,
            "moderator_id": moderator_id,
            "status": status,
            "unban_request_id": unban_request_id,
        }
        if resolution_text is not None:
            params["resolution_text"] = resolution_text

        route: Route = Route("PATCH", "moderation/unban_requests", params=params, token_for=token_for)
        return await self.request_json(route)

    def get_blocked_terms(
        self,
        broadcaster_id: str | int,
        moderator_id: str | int,
        token_for: str,
        first: int = 20,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[BlockedTerm]:
        params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id, "first": first}

        route: Route = Route("GET", "moderation/blocked_terms", params=params, token_for=token_for)

        async def converter(data: BlockedTermsResponseData, *, raw: Any) -> BlockedTerm:
            return BlockedTerm(data, http=self)

        iterator: HTTPAsyncIterator[BlockedTerm] = self.request_paginated(
            route, converter=converter, max_results=max_results
        )

        return iterator

    async def post_blocked_term(
        self,
        broadcaster_id: str | int,
        moderator_id: str | int,
        token_for: str,
        text: str,
    ) -> AddBlockedTermResponse:
        params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}
        data = {"text": text}

        route: Route = Route("POST", "moderation/blocked_terms", params=params, json=data, token_for=token_for)
        return await self.request_json(route)

    async def delete_blocked_term(
        self,
        broadcaster_id: str | int,
        moderator_id: str | int,
        token_for: str,
        id: str,
    ) -> None:
        params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id, "id": id}

        route: Route = Route("DELETE", "moderation/blocked_terms", params=params, token_for=token_for)
        return await self.request_json(route)

    async def delete_chat_message(
        self,
        broadcaster_id: str | int,
        moderator_id: str | int,
        token_for: str,
        message_id: str | None = None,
    ) -> None:
        params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}
        if message_id is not None:
            params["message_id"] = message_id

        route: Route = Route("DELETE", "moderation/chat", params=params, token_for=token_for)
        return await self.request_json(route)

    def get_moderated_channels(
        self,
        user_id: str | int,
        token_for: str,
        first: int = 20,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[PartialUser]:
        params = {"user_id": user_id, "first": first}

        route: Route = Route("GET", "moderation/channels", params=params, token_for=token_for)

        async def converter(data: ModeratedChannelsResponseData, *, raw: Any) -> PartialUser:
            return PartialUser(data["broadcaster_id"], data["broadcaster_login"], http=self)

        iterator: HTTPAsyncIterator[PartialUser] = self.request_paginated(
            route, converter=converter, max_results=max_results
        )

        return iterator

    def get_moderators(
        self,
        broadcaster_id: str | int,
        token_for: str,
        user_ids: list[str | int] | None = None,
        first: int = 20,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[PartialUser]:
        params: dict[str, str | int | list[str | int]] = {"broadcaster_id": broadcaster_id, "first": first}

        if user_ids is not None:
            params["user_id"] = user_ids

        route: Route = Route("GET", "moderation/moderators", params=params, token_for=token_for)

        async def converter(data: ModeratorsResponseData, *, raw: Any) -> PartialUser:
            return PartialUser(data["user_id"], data["user_login"], http=self)

        iterator: HTTPAsyncIterator[PartialUser] = self.request_paginated(
            route, converter=converter, max_results=max_results
        )

        return iterator

    async def post_channel_moderator(
        self,
        broadcaster_id: str | int,
        token_for: str,
        user_id: str | int,
    ) -> None:
        params = {"broadcaster_id": broadcaster_id, "user_id": user_id}

        route: Route = Route("POST", "moderation/moderators", params=params, token_for=token_for)
        return await self.request_json(route)

    async def delete_channel_moderator(
        self,
        broadcaster_id: str | int,
        token_for: str,
        user_id: str | int,
    ) -> None:
        params = {"broadcaster_id": broadcaster_id, "user_id": user_id}

        route: Route = Route("DELETE", "moderation/moderators", params=params, token_for=token_for)
        return await self.request_json(route)

    def get_vips(
        self,
        broadcaster_id: str | int,
        token_for: str,
        user_ids: list[str | int] | None = None,
        first: int = 20,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[PartialUser]:
        params: dict[str, str | int | list[str | int]] = {"broadcaster_id": broadcaster_id, "first": first}

        if user_ids is not None:
            params["user_id"] = user_ids

        route: Route = Route("GET", "channels/vips", params=params, token_for=token_for)

        async def converter(data: ModeratorsResponseData, *, raw: Any) -> PartialUser:
            return PartialUser(data["user_id"], data["user_login"], http=self)

        iterator: HTTPAsyncIterator[PartialUser] = self.request_paginated(
            route, converter=converter, max_results=max_results
        )

        return iterator

    async def add_vip(
        self,
        broadcaster_id: str | int,
        token_for: str,
        user_id: str | int,
    ) -> None:
        params = {"broadcaster_id": broadcaster_id, "user_id": user_id}

        route: Route = Route("POST", "channels/vips", params=params, token_for=token_for)
        return await self.request_json(route)

    async def delete_vip(
        self,
        broadcaster_id: str | int,
        token_for: str,
        user_id: str | int,
    ) -> None:
        params = {"broadcaster_id": broadcaster_id, "user_id": user_id}

        route: Route = Route("DELETE", "channels/vips", params=params, token_for=token_for)
        return await self.request_json(route)

    async def put_shield_mode_status(
        self,
        broadcaster_id: str | int,
        moderator_id: str | int,
        token_for: str,
        active: bool,
    ) -> ShieldModeStatusResponse:
        params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}
        data = {"is_active": active}

        route: Route = Route("PUT", "moderation/shield_mode", params=params, json=data, token_for=token_for)
        return await self.request_json(route)

    async def get_shield_mode_status(
        self,
        broadcaster_id: str | int,
        moderator_id: str | int,
        token_for: str,
    ) -> ShieldModeStatusResponse:
        params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}

        route: Route = Route("GET", "moderation/shield_mode", params=params, token_for=token_for)
        return await self.request_json(route)

    ### Polls ###

    def get_polls(
        self,
        broadcaster_id: str | int,
        token_for: str,
        ids: list[str] | None = None,
        first: int = 20,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[Poll]:
        params: dict[str, str | int | list[str]] = {"broadcaster_id": broadcaster_id, "first": first}

        if ids is not None:
            params["id"] = ids

        route: Route = Route("GET", "polls", params=params, token_for=token_for)

        async def converter(data: PollsResponseData, *, raw: Any) -> Poll:
            return Poll(data, http=self)

        iterator: HTTPAsyncIterator[Poll] = self.request_paginated(route, converter=converter, max_results=max_results)
        return iterator

    async def post_poll(
        self,
        broadcaster_id: str | int,
        title: str,
        choices: list[str],
        duration: int,
        token_for: str,
        channel_points_voting_enabled: bool = False,
        channel_points_per_vote: int | None = None,
    ) -> PollsResponse:
        _choices = [{"title": t} for t in choices]
        data = {
            "broadcaster_id": broadcaster_id,
            "title": title,
            "choices": _choices,
            "duration": duration,
            "channel_points_voting_enabled": channel_points_voting_enabled,
        }

        if channel_points_per_vote is not None:
            data["channel_points_per_vote"] = channel_points_per_vote

        route: Route = Route("POST", "polls", json=data, token_for=token_for)
        return await self.request_json(route)

    async def patch_poll(
        self,
        broadcaster_id: str | int,
        id: str,
        status: Literal["ARCHIVED", "TERMINATED"],
        token_for: str,
    ) -> PollsResponse:
        data = {
            "broadcaster_id": broadcaster_id,
            "id": id,
            "status": status,
        }

        route: Route = Route("PATCH", "polls", json=data, token_for=token_for)
        return await self.request_json(route)

    ### Predictions ###

    def get_predictions(
        self,
        broadcaster_id: str | int,
        token_for: str,
        ids: list[str] | None = None,
        first: int = 20,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[Prediction]:
        params: dict[str, str | int | list[str]] = {"broadcaster_id": broadcaster_id, "first": first}

        if ids is not None:
            params["id"] = ids

        route: Route = Route("GET", "predictions", params=params, token_for=token_for)

        async def converter(data: PredictionsResponseData, *, raw: Any) -> Prediction:
            return Prediction(data, http=self)

        iterator: HTTPAsyncIterator[Prediction] = self.request_paginated(
            route, converter=converter, max_results=max_results
        )

        return iterator

    async def post_prediction(
        self,
        broadcaster_id: str | int,
        title: str,
        outcomes: list[str],
        prediction_window: int,
        token_for: str,
    ) -> PredictionsResponse:
        _outcomes = [{"title": t} for t in outcomes]

        data = {
            "broadcaster_id": broadcaster_id,
            "title": title,
            "outcomes": _outcomes,
            "prediction_window": prediction_window,
        }

        route: Route = Route("POST", "predictions", json=data, token_for=token_for)
        return await self.request_json(route)

    async def patch_prediction(
        self,
        broadcaster_id: str | int,
        id: str,
        status: Literal["RESOLVED", "CANCELED", "LOCKED"],
        token_for: str,
        winning_outcome_id: str | None = None,
    ) -> PredictionsResponse:
        data = {
            "broadcaster_id": broadcaster_id,
            "id": id,
            "status": status,
        }

        if winning_outcome_id is not None:
            data["winning_outcome_id"] = winning_outcome_id

        route: Route = Route("PATCH", "predictions", json=data, token_for=token_for)
        return await self.request_json(route)

    ### Raids ###

    async def post_raid(
        self,
        from_broadcaster_id: str | int,
        to_broadcaster_id: str | int,
        token_for: str,
    ) -> StartARaidResponse:
        params = {"from_broadcaster_id": from_broadcaster_id, "to_broadcaster_id": to_broadcaster_id}
        route: Route = Route("POST", "raids", params=params, token_for=token_for)

        return await self.request_json(route)

    async def delete_raid(self, broadcaster_id: str | int, token_for: str) -> None:
        params = {"broadcaster_id": broadcaster_id}

        route: Route = Route("DELETE", "raids", params=params, token_for=token_for)
        return await self.request_json(route)

    ### Schedule ###

    ### Search ###

    def get_search_categories(
        self,
        *,
        query: str,
        first: int,
        token_for: str | None = None,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[Game]:
        params: dict[str, str | int | Sequence[str | int]] = {
            "query": query,
            "first": first,
        }
        route: Route = Route("GET", "search/categories", params=params, token_for=token_for)

        async def converter(data: GamesResponseData, *, raw: Any) -> Game:
            return Game(data, http=self)

        iterator: HTTPAsyncIterator[Game] = self.request_paginated(route, converter=converter, max_results=max_results)
        return iterator

    def get_search_channels(
        self,
        *,
        query: str,
        first: int,
        live: bool = False,
        token_for: str | None = None,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[SearchChannel]:
        params: dict[str, str | int] = {"query": query, "live_only": live, "first": first}
        route: Route = Route("GET", "search/channels", params=params, token_for=token_for)

        async def converter(data: SearchChannelsResponseData, *, raw: Any) -> SearchChannel:
            return SearchChannel(data, http=self)

        iterator: HTTPAsyncIterator[SearchChannel] = self.request_paginated(
            route, converter=converter, max_results=max_results
        )

        return iterator

    ### Streams ###

    def get_streams(
        self,
        *,
        first: int = 20,
        user_ids: list[int | str] | None = None,
        game_ids: list[int | str] | None = None,
        user_logins: list[int | str] | None = None,
        languages: list[str] | None = None,
        token_for: str | None = None,
        type: Literal["all", "live"] = "all",
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[Stream]:
        params: dict[str, str | int | Sequence[str | int]] = {
            "type": type,
            "first": first,
        }

        if user_ids is not None:
            params["user_id"] = user_ids
        if game_ids is not None:
            params["game_ids"] = game_ids
        if user_logins is not None:
            params["user_login"] = user_logins
        if languages is not None:
            params["language"] = languages

        route: Route = Route("GET", "streams", params=params, token_for=token_for)

        async def converter(data: StreamsResponseData, *, raw: Any) -> Stream:
            return Stream(data, http=self)

        iterator: HTTPAsyncIterator[Stream] = self.request_paginated(
            route, converter=converter, max_results=max_results
        )

        return iterator

    async def get_stream_key(self, broadcaster_id: str | int, token_for: str) -> StreamKeyResponse:
        params = {"broadcaster_id": broadcaster_id}

        route: Route = Route("GET", "streams/key", params=params, token_for=token_for)
        return await self.request_json(route)

    def get_followed_streams(
        self,
        *,
        user_id: str | int,
        token_for: str,
        first: int = 100,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[Stream]:
        params = {
            "user_id": user_id,
            "first": first,
        }

        route: Route = Route("GET", "streams/followed", params=params, token_for=token_for)

        async def converter(data: StreamsResponseData, *, raw: Any) -> Stream:
            return Stream(data, http=self)

        iterator: HTTPAsyncIterator[Stream] = self.request_paginated(
            route, converter=converter, max_results=max_results
        )

        return iterator

    async def post_stream_marker(
        self,
        user_id: str | int,
        token_for: str,
        description: str | None = None,
    ) -> CreateStreamMarkerResponse:
        data = {"user_id": user_id}
        if description is not None:
            data["description"] = description

        route: Route = Route("POST", "streams/markers", json=data, token_for=token_for)
        return await self.request_json(route)

    def get_stream_markers(
        self,
        *,
        user_id: str | int | None = None,
        video_id: str | None = None,
        token_for: str,
        first: int = 20,
        max_results: int | None = None,
    ) -> HTTPAsyncIterator[VideoMarkers]:
        params: dict[str, str | int] = {"first": first}

        if user_id is not None:
            params["user_id"] = user_id
        if video_id is not None:
            params["video_id"] = video_id

        route: Route = Route("GET", "streams/markers", params=params, token_for=token_for)

        async def converter(data: StreamMarkersResponseData, *, raw: Any) -> VideoMarkers:
            return VideoMarkers(data, http=self)

        iterator: HTTPAsyncIterator[VideoMarkers] = self.request_paginated(
            route, converter=converter, max_results=max_results
        )

        return iterator

    ### Subscriptions ###

    async def get_user_subscription(
        self,
        broadcaster_id: str | int,
        user_id: str | int,
        token_for: str,
    ) -> CheckUserSubscriptionResponse:
        params = {"broadcaster_id": broadcaster_id, "user_id": user_id}

        route: Route = Route("GET", "subscriptions/user", params=params, token_for=token_for)
        return await self.request_json(route)

    async def get_broadcaster_subscriptions(
        self,
        token_for: str,
        broadcaster_id: str | int,
        user_ids: list[str | int] | None = None,
        first: int = 20,
        max_results: int | None = None,
    ) -> BroadcasterSubscriptions:
        params: dict[str, list[str | int] | str | int] = {"broadcaster_id": broadcaster_id, "first": first}
        if user_ids is not None:
            params["user_id"] = user_ids

        route: Route = Route("GET", "subscriptions", params=params, token_for=token_for)

        async def converter(data: BroadcasterSubscriptionsResponseData) -> BroadcasterSubscription:
            return BroadcasterSubscription(data, http=self)

        iterator = self.request_paginated(route, converter=converter, max_results=max_results)
        data = await self.request_json(route)

        return BroadcasterSubscriptions(data, iterator)

    ### Tags ###

    ### Teams ###

    async def get_teams(
        self,
        *,
        team_name: str | None = None,
        team_id: str | None = None,
        token_for: str | None = None,
    ) -> TeamsResponse:
        params: dict[str, str] = {}

        if team_name:
            params = params = {"name": team_name}
        elif team_id:
            params = {"id": team_id}

        route: Route = Route("GET", "teams", params=params, token_for=token_for)
        return await self.request_json(route)

    async def get_channel_teams(
        self,
        *,
        broadcaster_id: str,
        token_for: str | None = None,
    ) -> ChannelTeamsResponse:
        params = {"broadcaster_id": broadcaster_id}

        route: Route = Route("GET", "teams/channel", params=params, token_for=token_for)
        return await self.request_json(route)

    ### Users ###

    async def get_users(
        self, ids: list[str | int] | None = None, logins: list[str] | None = None, token_for: str | None = None
    ) -> UsersResponse:
        params = {"id": ids, "login": logins}
        route: Route = Route("GET", "users", params=params, token_for=token_for)
        return await self.request_json(route)

    async def put_user(self, token_for: str, description: str | None) -> UpdateUserResponse:
        params = {"description": description} if description is not None else {"description": ""}
        route: Route = Route("PUT", "users", params=params, token_for=token_for)
        return await self.request_json(route)

    def get_user_block_list(
        self, broadcaster_id: str | int, token_for: str, first: int = 20, max_results: int | None = None
    ) -> HTTPAsyncIterator[PartialUser]:
        params = {"broadcaster_id": broadcaster_id, "first": first}

        route: Route = Route("GET", "users/blocks", params=params, token_for=token_for)

        async def converter(data: UserBlockListResponseData, *, raw: Any) -> PartialUser:
            return PartialUser(data["user_id"], data["user_login"], http=self)

        iterator: HTTPAsyncIterator[PartialUser] = self.request_paginated(
            route, converter=converter, max_results=max_results
        )

        return iterator

    async def put_block_user(
        self,
        user_id: str | int,
        token_for: str,
        source: Literal["chat", "whisper"] | None = None,
        reason: Literal["harassment", "spam", "other"] | None = None,
    ) -> None:
        params = {"target_user_id": user_id}
        if source is not None:
            params["source_context"] = source
        if reason is not None:
            params["reason"] = reason
        route: Route = Route("PUT", "users/blocks", params=params, token_for=token_for)
        return await self.request_json(route)

    async def delete_block_user(
        self,
        user_id: str | int,
        token_for: str,
    ) -> None:
        params = {"target_user_id": user_id}
        route: Route = Route("DELETE", "users/blocks", params=params, token_for=token_for)
        return await self.request_json(route)

    async def get_user_extensions(self, token_for: str) -> UserExtensionsResponse:
        route: Route = Route("GET", "users/extensions/list", token_for=token_for)
        return await self.request_json(route)

    async def get_active_user_extensions(
        self, *, user_id: str | int | None = None, token_for: str | None = None
    ) -> UserActiveExtensionsResponse:
        params: dict[str, str | int] = {"user_id": user_id} if user_id is not None else {}
        route: Route = Route("GET", "users/extensions", params=params, token_for=token_for)
        return await self.request_json(route)

    async def put_user_extensions(
        self, *, user_extensions: ActiveExtensions, token_for: str
    ) -> UpdateUserExtensionsResponse:
        data = {"data": user_extensions._to_dict()}
        route: Route = Route("PUT", "users/extensions", json=data, token_for=token_for)
        return await self.request_json(route)

    ### Videos ###

    def get_videos(
        self,
        *,
        ids: list[str | int] | None = None,
        user_id: str | int | None = None,
        game_id: str | int | None = None,
        language: str | None = None,
        period: Literal["all", "day", "month", "week"] = "all",
        sort: Literal["time", "trending", "views"] = "time",
        type: Literal["all", "archive", "highlight", "upload"] = "all",
        first: int = 20,
        max_results: int | None = None,
        token_for: str | None = None,
    ) -> HTTPAsyncIterator[Video]:
        params: dict[str, int | str | list[str | int]] = {"first": first, "period": period, "sort": sort, "type": type}

        if ids is not None:
            params["id"] = ids
        if user_id is not None:
            params["user_id"] = user_id
        if game_id is not None:
            params["game_id"] = game_id
        if language is not None:
            params["language"] = language

        route = Route("GET", "videos", params=params, token_for=token_for)

        async def converter(data: VideosResponseData, *, raw: Any) -> Video:
            return Video(data, http=self)

        iterator = self.request_paginated(route, converter=converter, max_results=max_results)
        return iterator

    async def delete_videos(self, ids: list[str | int], token_for: str) -> DeleteVideosResponse:
        params = {"id": ids}

        route: Route = Route("DELETE", "videos", params=params, token_for=token_for)
        return await self.request_json(route)

    ### Whispers ###

    async def post_whisper(self, from_user_id: str | int, to_user_id: str | int, token_for: str, message: str) -> None:
        params = {"from_user_id": from_user_id, "to_user_id": to_user_id}
        data = {"message": message}

        route: Route = Route("POST", "whispers", params=params, json=data, token_for=token_for)
        return await self.request_json(route)
