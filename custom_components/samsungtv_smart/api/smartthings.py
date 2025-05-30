""" Smartthings TV integration """

from __future__ import annotations

from asyncio import TimeoutError as AsyncTimeoutError
from datetime import timedelta
from enum import Enum
import json
import logging

from aiohttp import ClientConnectionError, ClientResponseError, ClientSession

from homeassistant.util import Throttle

API_BASEURL = "https://api.smartthings.com/v1"
API_DEVICES = f"{API_BASEURL}/devices"

DEVICE_TYPE_OCF = "OCF"
DEVICE_TYPE_NAME_TV = "Samsung OCF TV"

COMMAND_POWER_OFF = {
    "capability": "switch",
    "command": "off",
}
COMMAND_POWER_ON = {
    "capability": "switch",
    "command": "on",
}
COMMAND_REFRESH = {
    "capability": "refresh",
    "command": "refresh",
}
COMMAND_SET_SOURCE = {
    "capability": "mediaInputSource",
    "command": "setInputSource",
}
COMMAND_SET_VD_SOURCE = {
    "capability": "samsungvd.mediaInputSource",
    "command": "setInputSource",
}
COMMAND_MUTE = {
    "capability": "audioMute",
    "command": "mute",
}
COMMAND_UNMUTE = {
    "capability": "audioMute",
    "command": "unmute",
}
COMMAND_VOLUME_UP = {
    "capability": "audioVolume",
    "command": "volumeUp",
}
COMMAND_VOLUME_DOWN = {
    "capability": "audioVolume",
    "command": "volumeDown",
}
COMMAND_SET_VOLUME = {
    "capability": "audioVolume",
    "command": "setVolume",
}
COMMAND_CHANNEL_UP = {
    "capability": "tvChannel",
    "command": "channelUp",
}
COMMAND_CHANNEL_DOWN = {
    "capability": "tvChannel",
    "command": "channelDown",
}
COMMAND_SET_CHANNEL = {
    "capability": "tvChannel",
    "command": "setTvChannel",
}
COMMAND_PAUSE = {
    "capability": "mediaPlayback",
    "command": "pause",
}
COMMAND_PLAY = {
    "capability": "mediaPlayback",
    "command": "play",
}
COMMAND_STOP = {
    "capability": "mediaPlayback",
    "command": "stop",
}
COMMAND_FAST_FORWARD = {
    "capability": "mediaPlayback",
    "command": "fastForward",
}
COMMAND_REWIND = {
    "capability": "mediaPlayback",
    "command": "rewind",
}
COMMAND_SOUND_MODE = {
    "capability": "samsungvd.soundMode",
    "command": "setSoundMode",
}
COMMAND_PICTURE_MODE = {
    "capability": "samsungvd.pictureMode",
    "command": "setPictureMode",
}

DIGITAL_TV = "digitalTv"

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=10)
_LOGGER = logging.getLogger(__name__)


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
    }


def _command(command: dict, arguments: list | None = None):
    cmd = {"component": "main", **command}
    if arguments:
        cmd["arguments"] = arguments
    cmd_full = {"commands": [cmd]}
    return str(cmd_full)


class STStatus(Enum):
    """Represent SmartThings status."""

    STATE_OFF = 0
    STATE_ON = 1
    STATE_UNKNOWN = 2


class SmartThingsTV:
    """Class to read status for TV registered in SmartThings cloud."""

    def __init__(
        self,
        api_key: str,
        device_id: str,
        use_channel_info: bool = True,
        session: ClientSession | None = None,
    ):
        """Initialize SmartThingsTV."""
        self._api_key = api_key
        self._device_id = device_id
        self._use_channel_info = use_channel_info
        if session:
            self._session = session
            self._managed_session = False
        else:
            self._session = ClientSession()
            self._managed_session = True

        self._device_name = None
        self._state = STStatus.STATE_UNKNOWN
        self._prev_state = STStatus.STATE_UNKNOWN
        self._muted = False
        self._volume = 10
        self._source_list = None
        self._source_list_map = None
        self._source = ""
        self._channel = ""
        self._channel_name = ""
        self._sound_mode = None
        self._sound_mode_list = None
        self._sound_mode_list_map = None
        self._picture_mode = None
        self._picture_mode_list = None
        self._picture_mode_list_map = None

        self._is_forced_val = False
        self._forced_count = 0

    def __enter__(self):
        return self

    def __exit__(self, ext_type, ext_value, ext_traceback):
        pass

    @property
    def api_key(self) -> str:
        """Return current api_key."""
        return self._api_key

    @property
    def device_id(self) -> str:
        """Return current device_id."""
        return self._device_id

    @property
    def device_name(self) -> str:
        """Return current device_name."""
        return self._device_name

    @property
    def state(self):
        """Return current state."""
        return self._state

    @property
    def prev_state(self):
        """Return current state."""
        return self._prev_state

    @property
    def muted(self) -> bool:
        """Return current muted state."""
        return self._muted

    @property
    def volume(self) -> int:
        """Return current volume."""
        return self._volume

    @property
    def source(self) -> str:
        """Return current source."""
        return self._source

    @property
    def channel(self) -> str:
        """Return current channel."""
        return self._channel

    @property
    def channel_name(self) -> str:
        """Return current channel name."""
        return self._channel_name

    @property
    def source_list(self):
        """Return available source list."""
        return self._source_list

    @property
    def sound_mode(self):
        """Return current sound mode."""
        if self._state != STStatus.STATE_ON:
            return None
        return self._sound_mode

    @property
    def sound_mode_list(self):
        """Return available sound modes."""
        if self._state != STStatus.STATE_ON:
            return None
        return self._sound_mode_list

    @property
    def picture_mode(self):
        """Return current picture mode."""
        if self._state != STStatus.STATE_ON:
            return None
        return self._picture_mode

    @property
    def picture_mode_list(self):
        """Return available picture modes."""
        if self._state != STStatus.STATE_ON:
            return None
        return self._picture_mode_list

    def get_source_name(self, source_id: str) -> str:
        """Get source name based on source id."""
        if not self._source_list_map:
            return ""
        if source_id.upper() == DIGITAL_TV.upper():
            source_id = "dtv"
        for map_value in self._source_list_map:
            map_id = map_value.get("id")
            if map_id and map_id == source_id:
                return map_value.get("name", "")
        return ""

    def _get_source_list_from_map(self) -> list:
        """Return source list from source map."""
        if not self._source_list_map:
            return []
        source_list = []
        for map_value in self._source_list_map:
            if source_id := map_value.get("id"):
                if source_id.upper() == "DTV":
                    source_list.append(DIGITAL_TV)
                else:
                    source_list.append(source_id)
        return source_list

    def set_application(self, app_id):
        """Set running application info."""
        if self._use_channel_info:
            self._channel = ""
            self._channel_name = app_id
            self._is_forced_val = True
            self._forced_count = 0

    def _set_source(self, source):
        """Set current source info."""
        if source != self._source:
            self._source = source
            self._channel = ""
            self._channel_name = ""
            self._is_forced_val = True
            self._forced_count = 0

    @staticmethod
    def _load_json_list(dev_data, list_name):
        """Try load a list from string to json format."""
        load_list = []
        json_list = dev_data.get(list_name, {}).get("value")
        if json_list:
            try:
                load_list = json.loads(json_list)
            except (TypeError, ValueError):
                pass
        return load_list

    @staticmethod
    async def get_devices_list(api_key, session: ClientSession, device_label=""):
        """Get list of available SmartThings devices"""

        result = {}

        async with session.get(
            API_DEVICES,
            headers=_headers(api_key),
            raise_for_status=True,
        ) as resp:
            device_list = await resp.json()

        if device_list:
            _LOGGER.debug("SmartThings available devices: %s", str(device_list))

            for dev in device_list.get("items", []):
                if (device_id := dev.get("deviceId")) is None:
                    continue
                if dev.get("type", "") != DEVICE_TYPE_OCF:
                    continue

                label = dev.get("label", "")
                if device_label:
                    if label != device_label:
                        continue
                elif dev.get("deviceTypeName", "") != DEVICE_TYPE_NAME_TV:
                    continue

                result[device_id] = {
                    "name": dev.get("name", f"TV ID {device_id}"),
                    "label": label,
                }

        _LOGGER.info("SmartThings discovered TV devices: %s", str(result))

        return result

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    async def _device_refresh(self, **kwargs):
        """Refresh device status on SmartThings"""

        device_id = self._device_id
        if not device_id:
            return

        api_device = f"{API_DEVICES}/{device_id}"
        api_command = f"{api_device}/commands"

        if self._use_channel_info:
            async with self._session.post(
                api_command,
                headers=_headers(self._api_key),
                data=_command(COMMAND_REFRESH),
                raise_for_status=False,
            ) as resp:
                if resp.status == 409:
                    self._state = STStatus.STATE_OFF
                    return
                resp.raise_for_status()
                await resp.json()

        return

    async def _async_send_command(self, data_cmd):
        """Send a command via SmartThings"""
        device_id = self._device_id
        if not device_id:
            return
        if not data_cmd:
            return

        api_device = f"{API_DEVICES}/{device_id}"
        api_command = f"{api_device}/commands"

        async with self._session.post(
            api_command,
            headers=_headers(self._api_key),
            data=data_cmd,
            raise_for_status=True,
        ) as resp:
            await resp.json()

        await self._device_refresh()

    async def async_device_health(self):
        """Check device availability"""

        device_id = self._device_id
        if not device_id:
            return False

        api_device = f"{API_DEVICES}/{device_id}"
        api_device_health = f"{api_device}/health"

        # this get the real status of the device
        async with self._session.get(
            api_device_health,
            headers=_headers(self._api_key),
            raise_for_status=True,
        ) as resp:
            health = await resp.json()

        _LOGGER.debug(health)

        if health["state"] == "ONLINE":
            return True
        return False

    async def async_device_update(self, use_channel_info: bool = None):
        """Query device status on SmartThing"""

        device_id = self._device_id
        if not device_id:
            return

        if use_channel_info is not None:
            self._use_channel_info = use_channel_info

        api_device = f"{API_DEVICES}/{device_id}"
        api_device_status = f"{api_device}/states"
        # not used, just for reference
        # api_device_main_status = f"{api_device}/components/main/status"

        self._prev_state = self._state

        try:
            is_online = await self.async_device_health()
        except (
            AsyncTimeoutError,
            ClientConnectionError,
            ClientResponseError,
        ):
            self._state = STStatus.STATE_UNKNOWN
            return

        if is_online:
            self._state = STStatus.STATE_ON
        else:
            self._state = STStatus.STATE_OFF
            return

        await self._device_refresh()
        if self._state == STStatus.STATE_OFF:
            return

        async with self._session.get(
            api_device_status,
            headers=_headers(self._api_key),
            raise_for_status=True,
        ) as resp:
            data = await resp.json()

        _LOGGER.debug(data)

        dev_data = data.get("main", {})
        # device_state = data['main']['switch']['value']

        # Volume
        device_volume = dev_data.get("volume", {}).get("value", 0)
        if device_volume and device_volume.isdigit():
            self._volume = int(device_volume) / 100
        else:
            self._volume = 0

        # Muted state
        device_muted = dev_data.get("mute", {}).get("value", "")
        self._muted = device_muted == "mute"

        # Sound Mode
        self._sound_mode = dev_data.get("soundMode", {}).get("value")
        self._sound_mode_list = self._load_json_list(dev_data, "supportedSoundModes")
        self._sound_mode_list_map = self._load_json_list(dev_data, "supportedSoundModesMap")

        # Picture Mode
        self._picture_mode = dev_data.get("pictureMode", {}).get("value")
        self._picture_mode_list = self._load_json_list(dev_data, "supportedPictureModes")
        self._picture_mode_list_map = self._load_json_list(dev_data, "supportedPictureModesMap")

        # Sources and channel
        self._source_list_map = self._load_json_list(
            dev_data, "supportedInputSourcesMap"
        )
        # self._source_list = self._load_json_list(dev_data, "supportedInputSources")
        self._source_list = self._get_source_list_from_map()

        if self._is_forced_val and self._forced_count <= 0:
            self._forced_count += 1
            return
        self._is_forced_val = False
        self._forced_count = 0

        device_source = dev_data.get("inputSource", {}).get("value", "")
        device_tv_chan = dev_data.get("tvChannel", {}).get("value", "")
        device_tv_chan_name = dev_data.get("tvChannelName", {}).get("value", "")

        if device_source:
            if device_source.upper() == DIGITAL_TV.upper():
                device_source = DIGITAL_TV
        self._source = device_source
        # if the status is not refreshed this info may become not reliable
        if self._use_channel_info:
            self._channel = device_tv_chan
            self._channel_name = device_tv_chan_name
        else:
            self._channel = ""
            self._channel_name = ""

    async def async_turn_off(self):
        """Turn off TV via SmartThings"""
        data_cmd = _command(COMMAND_POWER_OFF)
        await self._async_send_command(data_cmd)

    async def async_turn_on(self):
        """Turn on TV via SmartThings"""
        data_cmd = _command(COMMAND_POWER_ON)
        await self._async_send_command(data_cmd)

    async def async_send_command(self, cmd_type, command=""):
        """Send a command to the device"""
        data_cmd = None

        if cmd_type == "setvolume":  # sets volume
            data_cmd = _command(COMMAND_SET_VOLUME, [int(command)])
        elif cmd_type == "stepvolume":  # steps volume up or down
            if command == "up":
                data_cmd = _command(COMMAND_VOLUME_UP)
            elif command == "down":
                data_cmd = _command(COMMAND_VOLUME_DOWN)
        elif cmd_type == "audiomute":  # mutes audio
            if command == "on":
                data_cmd = _command(COMMAND_MUTE)
            elif command == "off":
                data_cmd = _command(COMMAND_UNMUTE)
        elif cmd_type == "selectchannel":  # changes channel
            data_cmd = _command(COMMAND_SET_CHANNEL, [command])
        elif cmd_type == "stepchannel":  # steps channel up or down
            if command == "up":
                data_cmd = _command(COMMAND_CHANNEL_UP)
            elif command == "down":
                data_cmd = _command(COMMAND_CHANNEL_DOWN)
        else:
            return

        await self._async_send_command(data_cmd)

    async def async_select_source(self, source):
        """Select source"""
        # if source not in self._source_list:
        #     return
        data_cmd = _command(COMMAND_SET_SOURCE, [source])
        # set property to reflect new changes
        self._set_source(source)
        await self._async_send_command(data_cmd)

    async def async_select_vd_source(self, source):
        """Select source"""
        # if source not in self._source_list:
        #     return
        data_cmd = _command(COMMAND_SET_VD_SOURCE, [source])
        await self._async_send_command(data_cmd)

    async def async_set_sound_mode(self, mode):
        """Select sound mode"""
        if self._state != STStatus.STATE_ON:
            return
        mode_id = None
        for sound_mode in self._sound_mode_list_map:
            if sound_mode.get("name") == mode:
                mode_id = sound_mode.get("id")
                break

        if mode_id is None:
            raise InvalidSmartThingsSoundMode()

        data_cmd = _command(COMMAND_SOUND_MODE, [mode_id])
        await self._async_send_command(data_cmd)
        self._sound_mode = mode

    async def async_set_picture_mode(self, mode):
        """Select picture mode"""
        if self._state != STStatus.STATE_ON:
            return

        mode_id = None
        for picture_mode in self._picture_mode_list_map:
            if picture_mode.get("name") == mode:
                mode_id = picture_mode.get("id")
                break

        if mode_id is None:
            raise InvalidSmartThingsPictureMode()

        data_cmd = _command(COMMAND_PICTURE_MODE, [mode_id])
        await self._async_send_command(data_cmd)
        self._picture_mode = mode


class InvalidSmartThingsSoundMode(RuntimeError):
    """Selected sound mode is invalid."""


class InvalidSmartThingsPictureMode(RuntimeError):
    """Selected picture mode is invalid."""
