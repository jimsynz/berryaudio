import asyncio
import logging
import plexapi

from collections import namedtuple
from core.actor import SourceActor
from core.models import Album, Artist, Category, Image, RefType, Source, Track
from core.types import PlaybackControls
from plexapi.myplex import MyPlexAccount, MyPlexPinLogin
from plexapi.server import PlexServer
from plexapi.utils import searchType
from urllib.parse import quote

logger = logging.getLogger(__name__)

PLEX_LINK_URL = "https://plex.tv/link"
MUSIC_SECTION_TYPE = "artist"
PRODUCT_NAME = "Berry Audio"

# Relay connections proxy the stream through plex.tv with a bandwidth cap, which
# rules out direct play of hi-res files.
CONNECTION_LOCATIONS = ["local", "remote"]

SEARCH_VIEWS = (RefType.TRACK, RefType.ARTIST, RefType.ALBUM)


class PlexExtension(SourceActor):
    def __init__(self, name, core, db, config):
        super().__init__()
        self._name = name
        self._core = core
        self._db = db
        self._config = config

        plex_config = self._config.get(self._name, {})
        self._token = plex_config.get("token")
        self._server_id = plex_config.get("server")
        self._section_id = plex_config.get("section")
        self._baseurl = plex_config.get("baseurl")
        self._hostname = self._config.get("system", {}).get("hostname")

        self._account = None
        self._plex = None
        self._section = None
        self._pin_login = None

        self._source = Source(
            name="Plex",
            uri=self._name,
            controls=[
                PlaybackControls.SEEK,
                PlaybackControls.PLAY,
                PlaybackControls.PAUSE,
                PlaybackControls.NEXT,
                PlaybackControls.PREVIOUS,
                PlaybackControls.REPEAT,
                PlaybackControls.SHUFFLE,
                PlaybackControls.FAVOURITE,
            ],
            state={},
        )

    async def on_start(self):
        # plexapi's modules bind BASE_HEADERS by name, so it has to be mutated in place
        # rather than reassigned for the device to identify itself to Plex.
        plexapi.BASE_HEADERS["X-Plex-Product"] = PRODUCT_NAME
        plexapi.BASE_HEADERS["X-Plex-Device-Name"] = self._hostname or PRODUCT_NAME
        logger.info("Started")

    async def on_stop(self):
        logger.info("Stopped")

    async def on_event(self, message):
        pass

    async def on_start_service(self):
        logger.info("Starting Service")
        self._source.state.connected = bool(self._token)
        return self._source

    async def on_stop_service(self) -> bool:
        logger.info("Stopping Service")
        await self._core.request("playback.clear")
        return True

    async def on_config_update(self, config):
        updated_config = config[self._name]
        if not updated_config:
            return

        self._apply_config(updated_config)

    async def on_pin_login_start(self) -> dict:
        """Start linking this player to a Plex account and return the PIN to enter."""
        self._pin_login = MyPlexPinLogin()
        pin = await asyncio.to_thread(lambda: self._pin_login.pin)
        logger.info(f"Enter PIN {pin} at {PLEX_LINK_URL} to link this player")
        return {"pin": pin, "url": PLEX_LINK_URL}

    async def on_pin_login_poll(self) -> dict:
        """Check whether the PIN has been entered yet, storing the token once it has."""
        if self._pin_login is None:
            raise ValueError("No Plex PIN login in progress")

        linked = await asyncio.to_thread(self._pin_login.checkLogin)

        if linked:
            token = self._pin_login.token
            self._pin_login = None
            await self._store_config(
                {"token": token, "server": None, "section": None, "baseurl": None}
            )
            logger.info("Linked to Plex account")
            return {"status": "linked"}

        if self._pin_login.expired:
            self._pin_login = None
            return {"status": "expired"}

        return {"status": "pending"}

    async def on_servers(self) -> list[dict]:
        """List the Plex Media Servers reachable by the linked account."""
        return [
            {
                "name": resource.name,
                "server": resource.clientIdentifier,
                "owned": resource.owned,
                "online": resource.presence,
                "selected": resource.clientIdentifier == self._server_id,
            }
            for resource in await self._server_resources()
        ]

    async def on_libraries(self) -> list[dict]:
        """List the music libraries on the selected server."""
        return [
            {
                "name": section.title,
                "section": section.key,
                "selected": str(section.key) == str(self._section_id),
            }
            for section in await self._music_sections()
        ]

    async def on_directory(
        self,
        uri: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ):
        if not uri or uri == self._name:
            return self._directories()

        values = uri.split(":")

        match len(values):
            case 2:
                _, view = values
                return await self._browse(view, limit, offset)
            case 3:
                _, view, ref_id = values
                if ref_id.isdigit():
                    return [self._build(await self._fetch(view, ref_id))]
                return await self._browse(view, limit, offset, first_character=ref_id)
            case 4:
                _, view, ref_id, ref_type = values
                return await self._children(view, ref_id, ref_type)

        raise ValueError(f"Unsupported directory uri: {uri}")

    async def on_search(self, query: str) -> dict:
        section = await self._music_section()
        results = {}

        for view in SEARCH_VIEWS:
            items = await asyncio.to_thread(section.search, title=query, libtype=view)
            if items:
                results[view] = [self._build(item) for item in items]

        return results

    async def on_lookup_track(self, path: str) -> Track:
        return self._build(await self._fetch_track(path))

    async def on_playback_uri(self, path: str) -> str:
        track = await self._fetch_track(path)
        part = track.media[0].parts[0]
        plex = await self._connect_server()
        return plex.url(part.key, includeToken=True)

    def _directories(self):
        Item = namedtuple("Item", ["uri", "name", "type"])
        return [
            Item(uri=self._uri(RefType.ARTIST), name="Artists", type=RefType.CATEGORY),
            Item(uri=self._uri(RefType.ALBUM), name="Albums", type=RefType.CATEGORY),
            Item(uri=self._uri(RefType.TRACK), name="Tracks", type=RefType.CATEGORY),
            Item(
                uri=self._uri(RefType.PLAYLIST),
                name="Playlists",
                type=RefType.CATEGORY,
            ),
        ]

    def _uri(self, view: str, ref_id=None) -> str:
        if ref_id is None:
            return f"{self._name}:{view}"
        return f"{self._name}:{view}:{ref_id}"

    def _apply_config(self, values: dict) -> None:
        previous = (self._token, self._server_id, self._section_id, self._baseurl)

        if "token" in values:
            self._token = values["token"] or None

        if "server" in values:
            self._server_id = values["server"] or None

        if "section" in values:
            self._section_id = values["section"] or None

        if "baseurl" in values:
            self._baseurl = values["baseurl"] or None

        if previous != (self._token, self._server_id, self._section_id, self._baseurl):
            self._account = None
            self._plex = None
            self._section = None

    async def _store_config(self, values: dict) -> None:
        self._apply_config(values)
        await self._core.request("config.set", config={self._name: values})

    async def _connect_account(self) -> MyPlexAccount:
        if self._account is None:
            if not self._token:
                raise ValueError("Plex account is not linked")
            self._account = await asyncio.to_thread(MyPlexAccount, token=self._token)
        return self._account

    async def _connect_server(self) -> PlexServer:
        if self._plex is not None:
            return self._plex

        if self._baseurl:
            try:
                self._plex = await asyncio.to_thread(
                    PlexServer, self._baseurl, self._token
                )
                return self._plex
            except Exception as e:
                logger.warning(
                    f"Plex server unreachable at {self._baseurl}, rediscovering: {e}"
                )

        plex = await self._discover_server()
        baseurl = plex.url("")
        if baseurl != self._baseurl:
            await self._store_config({"baseurl": baseurl})

        self._plex = plex
        return self._plex

    async def _discover_server(self) -> PlexServer:
        servers = await self._server_resources()

        if self._server_id:
            resource = next(
                (s for s in servers if s.clientIdentifier == self._server_id), None
            )
            if resource is None:
                raise ValueError(f"Plex server {self._server_id} is not available")
        elif len(servers) == 1:
            resource = servers[0]
        else:
            raise ValueError("No Plex server selected")

        logger.info(f"Connecting to Plex server {resource.name}")
        return await asyncio.to_thread(
            resource.connect, locations=CONNECTION_LOCATIONS
        )

    async def _server_resources(self) -> list:
        account = await self._connect_account()
        resources = await asyncio.to_thread(account.resources)
        return [r for r in resources if "server" in (r.provides or "")]

    async def _music_sections(self) -> list:
        plex = await self._connect_server()
        sections = await asyncio.to_thread(plex.library.sections)
        return [s for s in sections if s.type == MUSIC_SECTION_TYPE]

    async def _music_section(self):
        if self._section is None:
            sections = await self._music_sections()

            if self._section_id:
                self._section = next(
                    (s for s in sections if str(s.key) == str(self._section_id)), None
                )
                if self._section is None:
                    raise ValueError(
                        f"Plex music library {self._section_id} is not available"
                    )
            elif len(sections) == 1:
                self._section = sections[0]
            else:
                raise ValueError("No Plex music library selected")

        return self._section

    async def _browse(
        self,
        view: str,
        limit: int | None,
        offset: int | None,
        first_character: str | None = None,
    ) -> list:
        if view == RefType.PLAYLIST:
            return await self._playlists()

        section = await self._music_section()

        if first_character:
            key = (
                f"/library/sections/{section.key}/firstCharacter"
                f"/{quote(first_character)}?type={searchType(view)}"
            )
            items = await asyncio.to_thread(
                section.fetchItems,
                key,
                container_start=offset,
                container_size=limit,
                maxresults=limit,
            )
        else:
            items = await asyncio.to_thread(
                section.search,
                libtype=view,
                sort="titleSort",
                container_start=offset,
                container_size=limit,
                maxresults=limit,
            )

        return [self._build(item) for item in items]

    async def _playlists(self) -> list:
        plex = await self._connect_server()
        section = await self._music_section()
        playlists = await asyncio.to_thread(
            plex.playlists, playlistType="audio", sectionId=section.key
        )
        return [self._build(playlist) for playlist in playlists]

    async def _children(self, view: str, ref_id: str, ref_type: str) -> list:
        item = await self._fetch(view, ref_id)

        match (view, ref_type):
            case (RefType.ARTIST, "albums"):
                children = await asyncio.to_thread(item.albums)
            case (RefType.ARTIST, "tracks") | (RefType.ALBUM, "tracks"):
                children = await asyncio.to_thread(item.tracks)
            case (RefType.PLAYLIST, "tracks"):
                children = await asyncio.to_thread(item.items)
            case _:
                raise ValueError(f"View type '{ref_type}' not supported for '{view}'")

        return [self._build(child) for child in children]

    async def _fetch(self, view: str, ref_id: str):
        plex = await self._connect_server()
        item = await asyncio.to_thread(plex.fetchItem, int(ref_id))

        if item.TYPE != view:
            raise ValueError(f"Plex item {ref_id} is a {item.TYPE}, not a {view}")

        return item

    async def _fetch_track(self, path: str):
        view, _, ref_id = path.partition(":")
        if view != RefType.TRACK or not ref_id.isdigit():
            raise ValueError(f"Invalid Plex track path: {path}")
        return await self._fetch(view, ref_id)

    def _build(self, item):
        match item.TYPE:
            case RefType.TRACK:
                return Track(**self._track_fields(item))
            case RefType.ALBUM:
                return Album(**self._album_fields(item))
            case RefType.ARTIST:
                return Artist(**self._artist_fields(item))
            case RefType.PLAYLIST:
                return Category(
                    uri=self._uri(RefType.PLAYLIST, item.ratingKey),
                    name=item.title,
                )

        raise ValueError(f"Unsupported Plex item type: {item.TYPE}")

    def _track_fields(self, track) -> dict:
        uri = self._uri(RefType.TRACK, track.ratingKey)
        images = self._images(track.thumbUrl)
        fields = {
            "uri": uri,
            "name": track.title,
            "favourite": self._is_favourite(uri),
            "images": images,
        }

        if track.grandparentTitle:
            fields["artists"] = frozenset(
                [
                    Artist(
                        uri=self._uri(RefType.ARTIST, track.grandparentRatingKey),
                        name=track.grandparentTitle,
                    )
                ]
            )

        if track.parentTitle:
            fields["albums"] = frozenset(
                [
                    Album(
                        uri=self._uri(RefType.ALBUM, track.parentRatingKey),
                        name=track.parentTitle,
                        date=track.year,
                        images=images,
                    )
                ]
            )

        if track.genres:
            fields["genre"] = track.genres[0].tag

        if track.index:
            fields["track_no"] = track.index

        if track.parentIndex:
            fields["disc_no"] = track.parentIndex

        if track.duration:
            fields["length"] = track.duration

        if track.year:
            fields["date"] = str(track.year)

        fields.update(self._media_fields(track))
        return fields

    def _album_fields(self, album) -> dict:
        uri = self._uri(RefType.ALBUM, album.ratingKey)
        fields = {
            "uri": uri,
            "name": album.title,
            "favourite": self._is_favourite(uri),
            "images": self._images(album.thumbUrl),
        }

        if album.parentTitle:
            fields["artists"] = frozenset(
                [
                    Artist(
                        uri=self._uri(RefType.ARTIST, album.parentRatingKey),
                        name=album.parentTitle,
                    )
                ]
            )

        if album.year:
            fields["date"] = album.year

        if album.leafCount:
            fields["num_tracks"] = album.leafCount

        return fields

    def _artist_fields(self, artist) -> dict:
        uri = self._uri(RefType.ARTIST, artist.ratingKey)
        fields = {
            "uri": uri,
            "name": artist.title,
            "favourite": self._is_favourite(uri),
            "images": self._images(artist.thumbUrl),
        }

        if artist.genres:
            fields["genre"] = artist.genres[0].tag

        if artist.countries:
            fields["country"] = artist.countries[0].tag

        return fields

    def _media_fields(self, track) -> dict:
        """Stream details Plex already knows, shown before GStreamer confirms them."""
        if not track.media:
            return {}

        media = track.media[0]
        fields = {}

        if media.audioCodec:
            fields["audio_codec"] = media.audioCodec

        if media.audioChannels:
            fields["channels"] = media.audioChannels

        if media.bitrate:
            # Plex reports kbps, the rest of berryaudio works in bps.
            fields["bitrate"] = media.bitrate * 1000

        if not media.parts:
            return fields

        part = media.parts[0]
        if part.size:
            fields["size"] = part.size

        streams = part.audioStreams()
        if not streams:
            return fields

        if streams[0].samplingRate:
            fields["sample_rate"] = streams[0].samplingRate

        if streams[0].bitDepth:
            fields["bit_depth"] = str(streams[0].bitDepth)

        return fields

    def _images(self, url) -> list:
        return [Image(uri=url)] if url else []

    def _is_favourite(self, uri):
        row = self._db.fetchone(
            'SELECT 1 FROM collection_favourite WHERE uri = ? LIMIT 1',
            (uri,)
        )
        return row is not None
