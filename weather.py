#!/usr/bin/env python3
#
# Not copyrighted at all by Johan Kanflo in 2019-2025 - CC0 applies
#
# Pull data from the inofficial Trafikverket weather stataion API (2MiB of data
# mind you...). Search for the weather station in the provided config file and
# post air temperature to the specified MQTT topic. If a measurement is older than
# 60 minutes it will be posted as _. You should handle this in your client :)

import sys
try:
    import requests
except ImportError:
    print("sudo -H python -m pip install requests")
    sys.exit(1)
import time
import logging
import socket
import argparse
import configparser
import json
try:
    from dateutil.parser import parse
except ImportError:
    print("sudo -H python -m pip install python-dateutil")
    sys.exit(1)
import datetime
try:
    import mqttwrapper
except ImportError:
    print("sudo -H python -m pip install git+https://github.com/kanflo/mqttwrapper")
    sys.exit(1)

measurement_too_old = "_"


def get_feed(save_file: bool = False) -> dict:
    """Get Trafikverket weather station feed

    Args:
        save_file (bool, optional): Save json. Defaults to False.

    Returns:
        dict: _description_
    """
    api_key = "707695ca4c704c93a80ebf62cf9af7b5"
    # If the API key ever changes, it can be found in a section looking like this:
    # <mapcomponent showroadconditionlayer="showroadconditionlayer"
    #               mapurl="https://maps.trafikinfo.trafikverket.se"
    #               apikey="707695ca4c704c93a80ebf62cf9af7b5"
    #               apiurl="https://api.trafikinfo.trafikverket.se/v2/data.json"></mapcomponent>
    # In Chrome, find the correct call to `data.json`, right click and select "Copy as cURL".
    # Then visit curlconverter.com to convert the Bash cURL request to Python:

    headers = {
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9,sv;q=0.8',
        'content-type': 'text/plain',
        'origin': 'https://www.trafikverket.se',
        'priority': 'u=1, i',
        'referer': 'https://www.trafikverket.se/',
        'sec-ch-ua': '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-site',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    }

    data = "<REQUEST>\n        <LOGIN authenticationkey='707695ca4c704c93a80ebf62cf9af7b5'/> \n        <QUERY  objecttype='WeatherMeasurepoint' schemaversion='2' sseurl='true'>\n            <FILTER>\n                <NOTLIKE name='Name' value='Fjärryta'/>\n            </FILTER>\n        </QUERY>\n    </REQUEST>".encode()
    response = requests.post('https://api.trafikinfo.trafikverket.se/v2/data.json', headers=headers, data=data)

    if response.status_code != 200:
        logging.error(f"Error: API access failed with {response.status_code}")
        return None
    if save_file:
        with open('weather.json', 'w') as f:
            f.write(response.text)
    return response.json()


def process_feed(j: dict, config: dict, max_age: int) -> bool:
    """Process the JSON feed

    Args:
        j (dict): JSON feed read from Trafikverket
        config (dict): Dictionary of our config file
        max_age (int): Max accepted measurement age in minutes

    Returns:
        bool: True if we published data
    """
    retain = "Retain" in config["MQTT"] and "True" in config["MQTT"]["Retain"]
    if j is None:
        logging.error("No JSON returned, seems the api was updated")
        mqttwrapper.publish(config["MQTT"]["MQTTTemperatureErrorTopic"], "API error", retain=retain)
        return False
    if "RESPONSE" not in j:
        logging.error("Response is invalid, seems the api was updated ('RESPONSE' is missing)")
        mqttwrapper.publish(config["MQTT"]["MQTTTemperatureErrorTopic"], "API error", retain=retain)
        return False
    if "RESULT" not in j["RESPONSE"]:
        logging.error("Response is invalid, seems the api was updated ('RESULT' is missing)")
        mqttwrapper.publish(config["MQTT"]["MQTTTemperatureErrorTopic"], "API error", retain=retain)
        return False
    if len(j["RESPONSE"]["RESULT"]) == 0:
        logging.error("Response is invalid, seems the api was updated ('RESULT.RESULT' is missing)")
        mqttwrapper.publish(config["MQTT"]["MQTTTemperatureErrorTopic"], "API error", retain=retain)
        return False
    try:
        for w in j["RESPONSE"]["RESULT"][0]["WeatherMeasurepoint"]:
            if w["Id"] == str(config["Weather"]["StationID"]):
                now = datetime.datetime.now()
                try:
                    name = w["Name"]
                except KeyError as e:
                    name = "noname"
                observation = w["Observation"]
                time = parse(observation["Sample"])
                time = time.replace(tzinfo=None)
                time_delta = now - time
                age = round(time_delta.total_seconds() / 60)
                if age > max_age:
                    logging.warning("Current measurement is too old, trying history")
                    time = parse(w["MeasurementHistory"][0]["MeasureTime"])
                    time_delta = now - time
                    age = round(time_delta.total_seconds() / 60)
                    #if age < max_age:
                    #    observation = w["MeasurementHistory"][0]

                try:
                    wind_speed = observation["Wind"][0]["Speed"]["Value"]
                except KeyError as e:
                    logging.error("JSON error", exc_info=e)
                    wind_speed = None
                try:
                    wind_gust = observation["Aggregated10minutes"]["Wind"]["SpeedMax"]["Value"]
                except KeyError as e:
                    logging.error("JSON error", exc_info=e)
                    wind_gust = None
                try:
                    wind_dir = observation["Wind"][0]["Direction"]["Value"]
                except KeyError as e:
                    logging.error("JSON error", exc_info=e)
                    wind_dir = None

                try:
                    # Precipitation looks like precipitationSnow or precipitationNoPrecipitation
                    # Chop off the leading 'precipitation'
                    precip_type = observation["Weather"]["Precipitation"].lower()
                except KeyError as e:
                    logging.error("JSON error", exc_info=e)
                    precip_type = None
                precip_amount = 0
                if "Aggregated30minutes" in observation:
                    try:
                        if "Precipitation" in observation["Aggregated30minutes"]:
                            if "Rain" in observation["Aggregated30minutes"]["Precipitation"] and observation["Aggregated30minutes"]["Precipitation"]["Rain"]:
                                precip_amount = observation["Aggregated30minutes"]["Precipitation"]["RainSum"]["Value"]
                            elif "Snow" in observation["Aggregated30minutes"]["Precipitation"] and observation["Precipitation"]["Snow"]:
                                precip_amount = observation["Aggregated30minutes"]["Precipitation"]["SnowSum"]["Solid"]["Value"]
                    except KeyError as e:
                        logging.error(f"JSON error in {observation}", exc_info=e)

                try:
                    air_temp = float(observation["Air"]["Temperature"]["Value"])
                    # Methinks decimals look silly
                    if air_temp < 0.1 and air_temp > -0.1:
                        logging.debug("Zeroing %.2f -> %.2f" % (air_temp, 0))
                        air_temp = 0
                    elif air_temp > 2 or air_temp < -2:
                        logging.debug("Rounding %.2f -> %.2f" % (air_temp, round(air_temp)))
                        air_temp = round(air_temp)
                except KeyError as e:
                    logging.error("JSON error", exc_info=e)
                    air_temp = None

                try:
                    dew_point = float(observation["Air"]["Dewpoint"]["Value"])
                except KeyError as e:
                    logging.error("JSON error", exc_info=e)
                    dew_point = None

                try:
                    rh = float(observation["Air"]["RelativeHumidity"]["Value"])
                except KeyError as e:
                    logging.error("JSON error", exc_info=e)
                    rh = None

                try:
                    road_temp = float(observation["Surface"]["Temperature"]["Value"])
                    # Methinks decimals look silly
                    if road_temp < 0.1 and road_temp > -0.1:
                        logging.debug("Zeroing %.2f -> %.2f" % (road_temp, 0))
                        road_temp = 0
                    elif road_temp > 2 or air_temp < -2:
                        logging.debug("Rounding %.2f -> %.2f" % (road_temp, round(road_temp)))
                        road_temp = round(road_temp)
                except KeyError as e:
                    logging.error("JSON error", exc_info=e)
                    road_temp = None


                if age < max_age:
                    logging.debug(f"{name}: road:{road_temp}°C air:{air_temp}°C wind:{wind_speed}m/s from {wind_dir} (gust {wind_gust}m/s), precipitation:{precip_type} dew point:{dew_point}°C rh:{rh}%")
                    observation = {"temperature": air_temp,
                                "road_temperature": road_temp,
                                "wind_speed": wind_speed,
                                "wind_gust": wind_gust,
                                "wind_direction": wind_dir,
                                "precip_type": precip_type,
                                "precip_amount": precip_amount,
                                "dew_point": dew_point,
                                "relative_himidity": rh,
                                }
                    observation = "%s" % observation
                    observation = observation.replace(" ", "").replace("'", "\"")
                    mqttwrapper.publish(config["MQTT"]["MQTTObservationTopic"], observation, retain=retain)
                    mqttwrapper.publish(config["MQTT"]["MQTTOutsideTemperatureTopic"], air_temp, retain=retain)
                    mqttwrapper.publish(config["MQTT"]["MQTTRoadTemperatureTopic"], road_temp, retain=retain)
                    mqttwrapper.publish(config["MQTT"]["MQTTWindSpeedTopic"], wind_speed, retain=retain)
                    mqttwrapper.publish(config["MQTT"]["MQTTWindGustTopic"], wind_gust, retain=retain)
                    mqttwrapper.publish(config["MQTT"]["MQTTWindDirectionTopic"], wind_dir, retain=retain)
                    mqttwrapper.publish(config["MQTT"]["MQTTPrecipitationTypeTopic"], precip_type, retain=retain)
                    mqttwrapper.publish(config["MQTT"]["MQTTPrecipitationAmountTopic"], precip_amount, retain=retain)
                    mqttwrapper.publish(config["MQTT"]["MQTTDewPointTopic"], dew_point, retain=retain)
                    mqttwrapper.publish(config["MQTT"]["MQTTRelativeHumidityTopic"], rh, retain=retain)
                    return True
                else:
                    logging.error("Measurement too old (%d minutes)" % age)
                    return False
    except KeyError as e:
        logging.error("JSON error", exc_info=e)
        mqttwrapper.publish(config["MQTT"]["MQTTTemperatureErrorTopic"], "JSON error", retain=retain)


def mqtt_callback(topic: str, payload: str):
    pass


def main():
    global config
    parser = argparse.ArgumentParser(description="This script pulls data form the inofficial Trafikverket weather stataion API")
    parser.add_argument("-v", "--verbose", help="Increase output verbosity", action="store_true")
    parser.add_argument("-c", "--config", action="store", help="Configuration file", default="sampleconfig.yml")
    parser.add_argument("-s", "--save", help="Save downloaded JSON to weather.json", action="store_true")
    parser.add_argument("-l", "--load", help="Use data in weather.json rather than calling the API", action="store_true")
    args = parser.parse_args()

    config = configparser.ConfigParser()
    try:
        config.read(args.config, encoding='utf-8')
    except Exception as e:
        logging.error("Failed to read config file: %s" % str(e))
        sys.exit(1)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, stream=sys.stdout,
                        format='%(asctime)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s',
                        datefmt='%Y%m%d %H:%M:%S')
    logging.info("---[ Starting %s ]---------------------------------------------" % sys.argv[0])

    broker = config["MQTT"]["MQTTBroker"]
    retain = "Retain" in config["MQTT"] and "True" in config["MQTT"]["Retain"]

    warned: bool = False
    while not mqttwrapper.is_connected():
        try:
            mqttwrapper.run_script(mqtt_callback, broker=broker, topics=["/nada"], retain=retain, blocking=False)
        except socket.gaierror:
            if not warned:
                logging.warning(f"Failed to connect to MQTT broker at {broker}, will retry")
                warned = True
            time.sleep(1)
    logging.info(f"Connected to MQTT broker at {broker}")

    if args.load:
        max_age = 99999999
        with open('weather.json', 'r') as f:
            j = json.loads(f.read())
            logging.info("Loaded weather JSON")
    else:
        # Disregard from meauserements older that 60 minutes
        max_age = 60

        j = get_feed(args.save)
    if j:
        success = False
        try:
            success = process_feed(j, config, max_age)
        except Exception as e:
            logging.error("Feed processing caused exception", exc_info=True)
        if not success:
            broker = config["MQTT"]["MQTTBroker"]
            retain = "Retain" in config["MQTT"] and "True" in config["MQTT"]["Retain"]
            mqttwrapper.publish(config["MQTT"]["MQTTOutsideTemperatureTopic"], measurement_too_old, retain)
            mqttwrapper.publish(config["MQTT"]["MQTTWindSpeedTopic"], measurement_too_old, retain)
            mqttwrapper.publish(config["MQTT"]["MQTTWindGustTopic"], measurement_too_old, retain)
            mqttwrapper.publish(config["MQTT"]["MQTTWindDirectionTopic"], measurement_too_old, retain)
            mqttwrapper.publish(config["MQTT"]["MQTTPrecipitationTypeTopic"], measurement_too_old, retain)
            mqttwrapper.publish(config["MQTT"]["MQTTPrecipitationAmountTopic"], measurement_too_old, retain)
            mqttwrapper.publish(config["MQTT"]["MQTTDewPointTopic"], measurement_too_old, retain=retain)
            mqttwrapper.publish(config["MQTT"]["MQTTRelativeHumidityTopic"], measurement_too_old, retain=retain)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error("Exception occurred in main", exc_info=True)
