#!/usr/bin/env python3
#
# Not copyrighted at all by Johan Kanflo in 2019 - CC0 applies
#
# Pull data form the inofficial Trafikverket weather stataion API (2MiB of data
# mind you...). Search for the weather station in the provided config file and
# post air temperature to the specified MQTT topic.

import sys
try:
    import requests
except ImportError:
    print("sudo -H pip3 install requests")
    sys.exit(1)
import logging
from logging.handlers import RotatingFileHandler
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
    return (stdout, stderr)


def get_feed():
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
    return resp.json()


def main():
    try:
        global config
        parser = argparse.ArgumentParser(description="This script pulls data form the inofficial Trafikverket weather stataion API")
        parser.add_argument("-v", "--verbose", help="Increase output verbosity", action="store_true")
        parser.add_argument("-c", "--config", action="store", help="Configuration file", default="sampleconfig.yml")
        args = parser.parse_args()

        config = configparser.ConfigParser()
        try:
            config.read(args.config, encoding='utf-8')
        except Exception as e:
            print("Failed to read config file: %s" % str(e))
            sys.exit(1)

        level = logging.DEBUG if args.verbose else logging.WARNING

        log_formatter = logging.Formatter('%(asctime)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s')
        logFile = config["DEFAULT"]["TempDir"] + ("/weather-%s.log" % config["DEFAULT"]["StationID"])
        try:
            my_handler = RotatingFileHandler(logFile, mode='a', maxBytes=100*1024, backupCount=1, encoding=None, delay=0)
        except FileNotFoundError:
            print("Failed to create %s" % logFile)
            sys.exit(1)

        my_handler.setFormatter(log_formatter)
        app_log = logging.getLogger()
        app_log.addHandler(my_handler)
        app_log.setLevel(level)

        logging.debug('---------------------------------------------------------')
        logging.debug('App started')

        j = get_feed()
        for w in j["RESPONSE"]["RESULT"][0]["WeatherStation"]:
            if w["Id"] == ("SE_STA_VVIS%s" % config["DEFAULT"]["StationID"]):
                now = datetime.datetime.now()
                name = w["Name"]
                time = parse(w["Measurement"]["MeasureTime"])
                time_delta = now - time
                age = round(time_delta.total_seconds() / 60)
                air_temp = float(w["Measurement"]["Air"]["Temp"])
                # Methinks decimals look silly
                if air_temp > 2 or air_temp < -2:
                    air_temp = round(air_temp)
                wind_dir = w["Measurement"]["Wind"]["DirectionText"]
                wind_speed = w["Measurement"]["Wind"]["Force"]
                wind_gust = w["Measurement"]["Wind"]["ForceMax"]
                precip = w["Measurement"]["Precipitation"]["Type"]
                if age < 45:
                    cmd = "mosquitto_pub -h %s -t %s -m %s" % (config["MQTT"]["MQTTBroker"], config["MQTT"]["MQTTOutsideTemperatureTopic"], air_temp)
                    print("%s: temperature %sC, wind %smps from %s (gust %smps), %s" % (name, air_temp, wind_speed, wind_dir.lower(), wind_gust, precip.lower()))
                else:
                    logging.error("Measurement too old (%d minutes)" % age)
                    cmd = "mosquitto_pub -h %s -t %s -m -" % (config["MQTT"]["MQTTBroker"], config["MQTT"]["MQTTOutsideTemperatureTopic"])
                cmd_run(cmd)

    except Exception as e:
        logging.error("Exception occurred", exc_info=True)
        tb = traceback.format_exc()
        print("Exception %s\n%s" % (str(e), tb))


if __name__ == "__main__":
    main()
