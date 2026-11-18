"""Support for thermostats (Amazon Smart Thermostat and other Alexa thermostats)."""

from typing import Any, override

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntity,
    ClimateEntityDescription,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AmazonConfigEntry, AmazonDevicesCoordinator, alexa_api_call
from .entity import AmazonEntity

PARALLEL_UPDATES = 1

THERMOSTAT_DESCRIPTION = ClimateEntityDescription(key="thermostat", name=None)

# Alexa ThermostatController modes <-> Home Assistant HVAC modes
ALEXA_TO_HA_MODE = {
    "HEAT": HVACMode.HEAT,
    "COOL": HVACMode.COOL,
    "AUTO": HVACMode.HEAT_COOL,
    "OFF": HVACMode.OFF,
}
HA_TO_ALEXA_MODE = {value: key for key, value in ALEXA_TO_HA_MODE.items()}

# Fan mode is a generic "mode" feature; find it by its friendly name
FAN_MODE_FEATURE_NAME = "fan mode"
FAN_CIRCULATE = "circulate"

# Limits of the Amazon Smart Thermostat (heating 40-87 F, cooling 53-90 F)
LIMITS = {
    UnitOfTemperature.FAHRENHEIT: (40.0, 90.0),
    UnitOfTemperature.CELSIUS: (4.5, 32.0),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmazonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Alexa Devices thermostats based on a config entry."""
    coordinator = entry.runtime_data

    known_devices: set[str] = set()

    def _check_device() -> None:
        current_devices = set(coordinator.data)
        known_devices.intersection_update(current_devices)
        new_devices = current_devices - known_devices
        if new_devices:
            known_devices.update(new_devices)
            async_add_entities(
                AmazonThermostatEntity(coordinator, serial_num, THERMOSTAT_DESCRIPTION)
                for serial_num in new_devices
                if "thermostat" in coordinator.data[serial_num].features
            )

    _check_device()
    entry.async_on_unload(coordinator.async_add_listener(_check_device))


class AmazonThermostatEntity(AmazonEntity, ClimateEntity):
    """Thermostat controlled through Alexa."""

    _attr_target_temperature_step = 1.0

    def __init__(
        self,
        coordinator: AmazonDevicesCoordinator,
        serial_num: str,
        description: ClimateEntityDescription,
    ) -> None:
        """Initialize the thermostat."""
        super().__init__(coordinator, serial_num, description)
        # values sent by us, shown until the next update from Amazon
        self._pending: dict[str, Any] = {}

        thermostat = self.device.features["thermostat"][""]
        modes = [
            ALEXA_TO_HA_MODE[mode]
            for mode in thermostat.supported_modes
            if mode in ALEXA_TO_HA_MODE
        ]
        self._attr_hvac_modes = modes or list(ALEXA_TO_HA_MODE.values())
        if HVACMode.OFF not in self._attr_hvac_modes:
            self._attr_hvac_modes.append(HVACMode.OFF)

        # fan: {Home Assistant fan mode: Alexa mode value}
        self._fan_instance: str | None = None
        self._fan_modes: dict[str, str] = {}
        for instance, feature in self.device.features.get("mode", {}).items():
            name = (feature.friendly_name or "").lower()
            if name != FAN_MODE_FEATURE_NAME:
                continue
            self._fan_instance = instance
            for option in feature.raw_configuration.get("modeOptions") or []:
                label = (
                    option.get("modeResources", {})
                    .get("friendlyName", {})
                    .get("value", {})
                    .get("text")
                )
                if label and option.get("value"):
                    self._fan_modes[label.lower()] = option["value"]
        if self._fan_modes:
            self._attr_fan_modes = list(self._fan_modes)

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Drop our pending values once Amazon reports fresh data."""
        self._pending.clear()
        super()._handle_coordinator_update()

    def _sensor(self, feature_name: str, property_name: str, instance: str = ""):
        """Return the sensor of a feature property, None if unknown or in error."""
        if (
            (feature := self.device.features.get(feature_name, {}).get(instance))
            is None
            or (sensor_key := feature.sensor_keys.get(property_name)) is None
            or (sensor := self.coordinator.device_sensor(self._serial_num, sensor_key))
            is None
            or sensor.error
        ):
            return None
        return sensor

    def _value(self, feature_name: str, property_name: str, instance: str = "") -> Any:
        """Return a value, preferring one we just sent."""
        if property_name in self._pending:
            return self._pending[property_name]
        if (sensor := self._sensor(feature_name, property_name, instance)) is None:
            return None
        return sensor.value

    def _float(self, property_name: str) -> float | None:
        """Return a set point as a float."""
        value = self._value("thermostat", property_name)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def _alexa_scale(self) -> str:
        """Return the scale Amazon uses for this thermostat."""
        for prop in ("lowerSetpoint", "upperSetpoint", "targetSetpoint"):
            if (sensor := self._sensor("thermostat", prop)) and sensor.scale:
                return str(sensor.scale).upper()
        if (sensor := self._sensor("temperatureSensor", "temperature")) and sensor.scale:
            return str(sensor.scale).upper()
        return "FAHRENHEIT"

    @property
    @override
    def native_temperature_unit(self) -> str:
        """Return the unit the thermostat works in."""
        if self._alexa_scale == "CELSIUS":
            return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.FAHRENHEIT

    @property
    @override
    def min_temp(self) -> float:
        """Return the lowest set point allowed."""
        return LIMITS[self.native_temperature_unit][0]

    @property
    @override
    def max_temp(self) -> float:
        """Return the highest set point allowed."""
        return LIMITS[self.native_temperature_unit][1]

    @property
    @override
    def supported_features(self) -> ClimateEntityFeature:
        """Return the controls that make sense for the current mode."""
        features = ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
        if self.hvac_mode == HVACMode.HEAT_COOL:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        elif self.hvac_mode in (HVACMode.HEAT, HVACMode.COOL):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if self._fan_modes:
            features |= ClimateEntityFeature.FAN_MODE
        return features

    @property
    @override
    def native_current_temperature(self) -> float | None:
        """Return the room temperature."""
        value = self._value("temperatureSensor", "temperature")
        return float(value) if value is not None else None

    @property
    @override
    def hvac_mode(self) -> HVACMode | None:
        """Return the current mode."""
        if (mode := self._value("thermostat", "thermostatMode")) is None:
            return None
        return ALEXA_TO_HA_MODE.get(str(mode).upper())

    @property
    @override
    def native_target_temperature(self) -> float | None:
        """Return the set point in heat or cool mode."""
        if (target := self._float("targetSetpoint")) is not None:
            return target
        if self.hvac_mode == HVACMode.HEAT:
            return self._float("lowerSetpoint")
        if self.hvac_mode == HVACMode.COOL:
            return self._float("upperSetpoint")
        return None

    @property
    @override
    def native_target_temperature_low(self) -> float | None:
        """Return the heat-to set point."""
        return self._float("lowerSetpoint")

    @property
    @override
    def native_target_temperature_high(self) -> float | None:
        """Return the cool-to set point."""
        return self._float("upperSetpoint")

    @property
    @override
    def fan_mode(self) -> str | None:
        """Return the fan mode."""
        if self._fan_instance is None:
            return None
        if (value := self._value("mode", "mode", self._fan_instance)) is None:
            return None
        return next(
            (name for name, alexa in self._fan_modes.items() if alexa == str(value)),
            None,
        )

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return whether the thermostat follows its schedule."""
        attributes: dict[str, Any] = {}
        if (enabled := self._value("thermostatSchedule", "scheduleEnabled")) is not None:
            attributes["schedule_enabled"] = bool(enabled)
        return attributes

    async def _call(
        self,
        feature_name: str,
        operation_name: str,
        command_payload: dict[str, Any] | None = None,
        instance: str | None = None,
    ) -> None:
        """Invoke a feature operation on the thermostat."""
        async with alexa_api_call(self.coordinator):
            await self.coordinator.api.call_feature_operation(
                self.device,
                feature_name,
                operation_name,
                command_payload=command_payload,
                instance=instance,
            )

    def _setpoint(self, value: float) -> dict[str, Any]:
        """Return a set point in the form Amazon expects (a number, not text)."""
        return {"value": round(float(value)), "scale": self._alexa_scale}

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Change the mode."""
        if (alexa_mode := HA_TO_ALEXA_MODE.get(hvac_mode)) is None:
            raise ServiceValidationError(f"Unsupported mode: {hvac_mode}")
        await self._call(
            "thermostat", "setThermostatMode", {"thermostatMode": alexa_mode}
        )
        self._pending["thermostatMode"] = alexa_mode
        self.async_write_ha_state()

    @override
    async def async_turn_off(self) -> None:
        """Turn the thermostat off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    @override
    async def async_turn_on(self) -> None:
        """Turn the thermostat on in automatic mode."""
        await self.async_set_hvac_mode(HVACMode.HEAT_COOL)

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Change the set point or set points."""
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(hvac_mode)

        low = kwargs.get(ATTR_TARGET_TEMP_LOW)
        high = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        if low is not None or high is not None:
            low = low if low is not None else self.native_target_temperature_low
            high = high if high is not None else self.native_target_temperature_high
            if low is None or high is None:
                raise ServiceValidationError("Both set points are needed")
            await self._call(
                "thermostat",
                "setTargetSetpoint",
                {
                    "lowerSetpoint": self._setpoint(low),
                    "upperSetpoint": self._setpoint(high),
                },
            )
            self._pending["lowerSetpoint"] = round(float(low))
            self._pending["upperSetpoint"] = round(float(high))
        elif (target := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self._call(
                "thermostat",
                "setTargetSetpoint",
                {"targetSetpoint": self._setpoint(target)},
            )
            self._pending["targetSetpoint"] = round(float(target))
        self.async_write_ha_state()

    @override
    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Change the fan mode."""
        if self._fan_instance is None or fan_mode not in self._fan_modes:
            raise ServiceValidationError(f"Unsupported fan mode: {fan_mode}")
        await self._call(
            "mode", "setMode", {"mode": self._fan_modes[fan_mode]}, self._fan_instance
        )
        self._pending["mode"] = self._fan_modes[fan_mode]
        self.async_write_ha_state()

