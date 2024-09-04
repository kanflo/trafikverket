#!/usr/bin/env python3
#
# Not copyrighted at all by Johan Kanflo in 2019 - CC0 applies
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
    url = "https://api.trafikinfo.trafikverket.se/v2/data.json"
    data = "<REQUEST><LOGIN authenticationkey='%s'/><QUERY  lastmodified='false' objecttype='WeatherStation' schemaversion='1' includedeletedobjects='true' sseurl='true'><FILTER><NOTLIKE name='Name' value='/Fjärryta/' /></FILTER></QUERY></REQUEST>" % (api_key)
    headers = {}
    headers['Origin'] = 'https://www.trafikverket.se'
    headers['Accept-Encoding'] = 'gzip, deflate, br'
    headers['Accept-Language'] = 'en-US,en;q=0.9,sv;q=0.8'
    headers['User-Agent'] = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.77 Safari/537.36'
    headers['Content-Type'] = 'text/plain'
    headers['Accept'] = 'application/json, text/javascript, */*; q=0.01'
    headers['cache-control'] = 'no-cache'
    headers['Referer'] = 'https://www.trafikverket.se/trafikinformation/vag/?TrafficType=personalTraffic&map=7%2F393050.38%2F6185337.96%2F&Layers=TrafficSituation%2BRoadWork%2BRoadWeather%2B'
    headers['Connection'] = 'keep-alive'
    headers['Sec-Fetch-Dest'] = 'empty'
    headers['Sec-Fetch-Mode'] = 'cors'
    headers['Sec-Fetch-Site'] = 'same-site'
    headers['sec-ch-ua'] = 'Not A;Brand";v="99", "Chromium";v="102", "Google Chrome";v="102"'
    headers['sec-ch-ua-mobile'] = '?0'
    headers['sec-ch-ua-platform'] = '"Linux"'

    resp = requests.post(url, headers=headers, data=data)
    if resp.status_code != 200:
        logging.error("Error: API access failed with %d" % resp.status_code)
        return None
    if save_file:
        with open('weather.json', 'w') as f:
            f.write(resp.text)
    return resp.json()


def process_feed(j: dict, config: dict, max_age: int) -> bool:
    """Process the JSON feed

    Args:
        j (dict): JSON feed read from Trafikverket
        config (dict): Dictionary of our config file
        max_age (int): Max accepted measurement age in minutes

    Returns:
        bool: True if we published data
    """
    p = []
    broker = config["MQTT"]["MQTTBroker"]

    retain = "Retain" in config["MQTT"] and "True" in config["MQTT"]["Retain"]
    if j is None:
        logging.error("No JSON returned, seems the api was updated")
        return False
    if "RESPONSE" not in j:
        logging.error("Response is invalid, seems the api was updated ('RESPONSE' is missing)")
        return False
    if "RESULT" not in j["RESPONSE"]:
        logging.error("Response is invalid, seems the api was updated ('RESULT' is missing)")
        return False
    if len(j["RESPONSE"]["RESULT"]) == 0:
        logging.error("Response is invalid, seems the api was updated ('RESULT.RESULT' is missing)")
        return False
    for w in j["RESPONSE"]["RESULT"][0]["WeatherStation"]:
        if w["Id"] == ("SE_STA_VVIS%s" % config["Weather"]["StationID"]):
            now = datetime.datetime.now()
            try:
                name = w["Name"]
            except KeyError:
                name = "noname"
            meas = w["Measurement"]
            time = parse(meas["MeasureTime"])
            time = time.replace(tzinfo=None)
            time_delta = now - time
            age = round(time_delta.total_seconds() / 60)
            if age > max_age:
                logging.warning("Current measurement is too old, trying history")
                time = parse(w["MeasurementHistory"][0]["MeasureTime"])
                time_delta = now - time
                age = round(time_delta.total_seconds() / 60)
                if age < max_age:
                    meas = w["MeasurementHistory"][0]

            try:
                wind_speed = meas["Wind"]["Force"]
            except KeyError:
                wind_speed = None
            try:
                wind_gust = meas["Wind"]["ForceMax"]
            except KeyError:
                wind_gust = None
            try:
                # Precipitation looks like precipitationSnow or precipitationNoPrecipitation
                # Chop off the leading 'precipitation'
                precip_type = meas["Precipitation"]["TypeIconId"]
                precip_type = precip_type[len("precipitation"):].lower()
                if not precip_type in p:
                    p.append(precip_type)
            except KeyError:
                precip_type = None
            try:
                precip_amount = meas["Precipitation"]["Amount"]
            except KeyError:
                precip_amount = 0

            air_temp = float(meas["Air"]["Temp"])
            # Methinks decimals look silly
            if air_temp < 0.1 and air_temp > -0.1:
                logging.debug("Zeroing %.2f -> %.2f" % (air_temp, 0))
                air_temp = 0
            elif air_temp > 2 or air_temp < -2:
                logging.debug("Rounding %.2f -> %.2f" % (air_temp, round(air_temp)))
                air_temp = round(air_temp)

            road_temp = float(meas["Road"]["Temp"])
            # Methinks decimals look silly
            if road_temp < 0.1 and road_temp > -0.1:
                logging.debug("Zeroing %.2f -> %.2f" % (road_temp, 0))
                road_temp = 0
            elif road_temp > 2 or air_temp < -2:
                logging.debug("Rounding %.2f -> %.2f" % (road_temp, round(road_temp)))
                road_temp = round(road_temp)

            wind_dir = meas["Wind"]["Direction"]

            if age < max_age:
                logging.debug("%s: temperature %s°C, wind %sm/s from %s (gust %sm/s), %s" % (name, air_temp, wind_speed, wind_dir, wind_gust, precip_type.lower()))
                observation = {"temperature": air_temp,
                               "road_temperature": air_temp,
                               "wind_speed": wind_speed,
                               "wind_gust": wind_gust,
                               "wind_direction": wind_dir,
                               "precip_type": precip_type,
                               "precip_amount": precip_amount
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
                return True
            else:
                logging.error("Measurement too old (%d minutes)" % age)
                return False


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
    mqttwrapper.run_script(mqtt_callback, broker=broker, topics=["/nada"], retain=retain, blocking=False)
    while not mqttwrapper.is_connected():
        time.sleep(1)
    logging.info("Connected to MQTT broker")

    if args.load:
        max_age = 99999999
        with open('weather.json', 'r') as f:
            j = json.loads(f.read())
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


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error("Exception occurred in main", exc_info=True)
