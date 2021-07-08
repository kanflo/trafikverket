#!/usr/bin/env python3
#
# Not copyrighted at all by Johan Kanflo in 2019 - CC0 applies
#
# Pull data form the inofficial Trafikverket weather stataion API (2MiB of data
# mind you...). Search for the weather station in the provided config file and
# post air temperature to the specified MQTT topic. If a measurement is older than
# 30 minutes it will be posted as 999. You should handle this in your client :)

import sys
try:
    import requests
except ImportError:
    print("sudo -H pip3 install requests")
    sys.exit(1)
import logging
import argparse
import configparser
import traceback
import json
try:
    from dateutil.parser import parse
except ImportError:
    print("sudo -H pip3 install python-dateutil")
    sys.exit(1)
import datetime
from subprocess import Popen, PIPE

measurement_too_old = "999"


def cmd_run(cmd):
    """
    @brief      Simple popen wrapper

    @return     A tuple consisting of (stdout, stderr)
    """
    logging.debug(cmd)
    temp = []
    # Duplicated spaces will mess things up...
    for arg in cmd.split(" "):
        if len(arg) > 0:
            temp.append(arg)
    process = Popen(temp, stdout=PIPE, stderr=PIPE)
    stdout, stderr = process.communicate()
    if len(stderr) > 0:
        logging.error("Command failed: %s" % (stderr.decode('utf-8')))
    return (stdout, stderr)


def mqtt_publish(broker, topic, message, retain = False):
    """
    Publish topic to broker

    :param      broker:   Address of broker
    :type       broker:   String
    :param      topic:    Topic
    :type       topic:    String
    :param      message:  Message
    :type       message:  String

    :returns:   Nothing
    """
    if message is None:
        logging.error("Cannot publish 'None' messages on topic %s" % (topic))
        return
    cmd = "mosquitto_pub -h %s -t %s -m %s" % (broker, topic, message)
    if retain:
        cmd += " --retain"
    cmd_run(cmd)



def get_feed(save_file = False):
    """
    @brief      Get Trafikverket weather station feed

    @return     The feed as JSON.
    """
    url = "https://api.trafikinfo.trafikverket.se/v1.3/data.json"
    data = '<REQUEST><LOGIN authenticationkey=\'707695ca4c704c93a80ebf62cf9af7b5\'/><QUERY  lastmodified=\'false\' objecttype=\'WeatherStation\'><FILTER></FILTER></QUERY></REQUEST>'
    headers = {}
    headers['Origin'] = 'https://www.trafikverket.se'
    headers['Accept-Encoding'] = 'gzip, deflate, br'
    headers['Accept-Language'] = 'en-US,en;q=0.9,sv;q=0.8,da;q=0.7'
    headers['User-Agent'] = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.77 Safari/537.36'
    headers['Content-Type'] = 'text/xml'
    headers['Accept'] = 'application/json, text/javascript, */*; q=0.01'
    headers['cache-control'] = 'no-cache'
    headers['Referer'] = 'https://www.trafikverket.se/trafikinformation/vag/?TrafficType=personalTraffic&map=7%2F393050.38%2F6185337.96%2F&Layers=TrafficSituation%2BRoadWork%2BRoadWeather%2B'
    headers['Connection'] = 'keep-alive'
    resp = requests.post(url, headers=headers, data=data)
    if resp.status_code != 200:
        logging.error("Error: API access failed with %d" % resp.status_code)
        return None
    if save_file:
        with open('weather.json', 'w') as f:
            f.write(resp.text)
    return resp.json()


def process_feed(j, config, max_age):
    """
    Process the JSON feed

    :param      j:    JSON feed read from Trafikverket
    :type       j:    JSON
    :param      config:    Dictionary of our config file
    :type       config:    dict
    :param      max_age:    Max accepted measurement age
    :type       max_age:    int
    """
    p = []
    broker = config["MQTT"]["MQTTBroker"]

    retain = "Retain" in config["MQTT"] and "True" in config["MQTT"]["Retain"]

    for w in j["RESPONSE"]["RESULT"][0]["WeatherStation"]:
        try:
            wind_speed = w["Measurement"]["Wind"]["Force"]
        except KeyError:
            wind_speed = None
        try:
            wind_gust = w["Measurement"]["Wind"]["ForceMax"]
        except KeyError:
            wind_gust = None
        try:
            # Precipitation looks loke precipitationSnow or precipitationNoPrecipitation
            # Chop off the leading 'precipitation'
            precip_type = w["Measurement"]["Precipitation"]["TypeIconId"]
            precip_type = precip_type[len("precipitation"):].lower()
            if not precip_type in p:
                p.append(precip_type)
        except KeyError:
            precip_type = None
        try:
            precip_amount = w["Measurement"]["Precipitation"]["Amount"]
        except KeyError:
            precip_amount = 0

        if w["Id"] == ("SE_STA_VVIS%s" % config["DEFAULT"]["StationID"]):
            print(w["Measurement"]["Air"])
            print(w["Measurement"]["Wind"])
            print(w["Measurement"]["Precipitation"])

            now = datetime.datetime.now()
            name = w["Name"]
            time = parse(w["Measurement"]["MeasureTime"])
            time_delta = now - time
            age = round(time_delta.total_seconds() / 60)
            air_temp = float(w["Measurement"]["Air"]["Temp"])
            # Methinks decimals look silly
            if air_temp < 0.1 and air_temp > -0.1:
                logging.debug("Zeroing %.2f -> %.2f" % (air_temp, 0))
                air_temp = 0
            elif air_temp > 2 or air_temp < -2:
                logging.debug("Rounding %.2f -> %.2f" % (air_temp, round(air_temp)))
                air_temp = round(air_temp)
            wind_dir = w["Measurement"]["Wind"]["Direction"]

            if age < max_age:
                logging.debug("%s: temperature %s°C, wind %smps from %s (gust %smps), %s" % (name, air_temp, wind_speed, wind_dir, wind_gust, precip_type.lower()))
                observation = {"temperature": air_temp,
                               "wind_speed": wind_speed,
                               "wind_gust": wind_gust,
                               "wind_direction": wind_dir,
                               "precip_type": precip_type,
                               "precip_amount": precip_amount
                               }
                observation = "%s" % observation
                observation = observation.replace(" ", "").replace("'", "\"")
                mqtt_publish(broker, config["MQTT"]["MQTTObservationTopic"], observation, retain)
                mqtt_publish(broker, config["MQTT"]["MQTTOutsideTemperatureTopic"], air_temp, retain)
                mqtt_publish(broker, config["MQTT"]["MQTTWindSpeedTopic"], wind_speed, retain)
                mqtt_publish(broker, config["MQTT"]["MQTTWindGustTopic"], wind_gust, retain)
                mqtt_publish(broker, config["MQTT"]["MQTTWindDirectionTopic"], wind_dir, retain)
                mqtt_publish(broker, config["MQTT"]["MQTTPrecipitationTypeTopic"], precip_type, retain)
                mqtt_publish(broker, config["MQTT"]["MQTTPrecipitationAmountTopic"], precip_amount, retain)
            else:
                logging.error("Measurement too old (%d minutes)" % age)
                mqtt_publish(broker, config["MQTT"]["MQTTOutsideTemperatureTopic"], measurement_too_old, retain)
                mqtt_publish(broker, config["MQTT"]["MQTTWindSpeedTopic"], measurement_too_old, retain)
                mqtt_publish(broker, config["MQTT"]["MQTTWindGustTopic"], measurement_too_old, retain)
                mqtt_publish(broker, config["MQTT"]["MQTTWindDirectionTopic"], measurement_too_old, retain)
                mqtt_publish(broker, config["MQTT"]["MQTTPrecipitationTypeTopic"], measurement_too_old, retain)
                mqtt_publish(broker, config["MQTT"]["MQTTPrecipitationAmountTopic"], measurement_too_old, retain)
    logging.debug("Found the following precipitations: %s" % (p))


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
        print("Failed to read config file: %s" % str(e))
        sys.exit(1)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, stream=sys.stdout,
                        format='%(asctime)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s',
                        datefmt='%Y%m%d %H:%M:%S')
    logging.info("---[ Starting %s ]---------------------------------------------" % sys.argv[0])

    if args.load:
        max_age = 99999999
        with open('weather.json', 'r') as f:
            j = json.loads(f.read())
    else:
        # Disregard from meauserements older that 60 minutes
        max_age = 60

        j = get_feed(args.save)
    process_feed(j, config, max_age)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error("Exception occurred in main", exc_info=True)
