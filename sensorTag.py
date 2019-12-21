# -*- coding: utf-8 -*-
# while True:
#     SensorTagをスキャンし、見つけたデバイスに接続し、
#     温度、湿度、気圧、照度、バッテリーレベルを取得し、Ambientに送信
#
import bluepy
import time
import sys
import argparse
import ambient
import threading
import http.server
import redis

g_sensor_data = []
global_lock = threading.Lock()   # LOCK OBJECT

class ambient_control(threading.Thread):
    def __init__(self):
        self.channelId = 0
        self.writeKey = '0'
        super(ambient_control, self).__init__()
    def set_key(self,channel_id,write_key):
        self.channelId = channel_id
        self.writeKey = write_key
        self.am = ambient.Ambient(channel_id, write_key)
    def send_param(self,data):
        self.send(data)
    def run(self):
        global g_sensor_data
        global global_lock

        while True:
            time.sleep(20.0)
            print(g_sensor_data)
            if not g_sensor_data :
                global_lock.acquire()
                for d in g_sensor_data:
                    print(d)
                global_lock.release()



class sensor_control(threading.Thread):

    def __init__(self,interval = 120,time_out = 5.0):
        self.interval = interval
        self.time_out = time_out
        self.scanner = bluepy.btle.Scanner(0)   # bluepyのScannerインスタンスを生成
        self.data = []                          # sensor TagのDeviceオブジェクトと読み込みデータを格納する
        self.scan()                             # 初回スキャン
        super(sensor_control, self).__init__()
    def scan(self):
        global g_sensor_data
        global global_lock

        print('scanning tag...')
        self.data.clear()
        devices = self.scanner.scan(self.time_out)  # BLEをスキャンする
        for d in devices:
            for (sdid, desc, val) in d.getScanData():
                if sdid == 9 and val == 'CC2650 SensorTag': # ローカルネームが'CC2650 SensorTag'のものを探す
                    dev = d
                    print('found SensorTag, addr = %s' % dev.addr)
                    tag = bluepy.sensortag.SensorTag(dev.addr) # 見つけたデバイスに接続する
                    # 見つけたデバイスのデータを一つにまとめる
                    self.data.append({
                        "device" : d,
                        "tag" : tag,
                        "data": {'d1':0,'d2':0,'d3':0,'d4':0,'d5':0},
                        "channelId":0,
                        "write_key":0
                        })
                    global_lock.acquire()
                    g_sensor_data.append(self.data[-1])
                    global_lock.release()
        sys.stdout.flush()
    def get_data(self):

        for d in self.data:
            try:
                tag = d["tag"]
                get_data = d["data"]
                tag.IRtemperature.enable()
                tag.humidity.enable()
                tag.barometer.enable()
                tag.battery.enable()
                tag.lightmeter.enable()
                time.sleep(1.0)
                # Some sensors (e.g., temperature, accelerometer) need some time for initialization.
                # Not waiting here after enabling a sensor, the first read value might be empty or incorrect.
                get_data['d1'] = tag.IRtemperature.read()[0]  # set ambient temperature to d1
                get_data['d2'] = tag.humidity.read()[1]  # set humidity to d2
                get_data['d3'] = tag.barometer.read()[1]  # set barometer to d3
                get_data['d5'] = tag.lightmeter.read()  # set light to d5
                get_data['d4'] = tag.battery.read()  # set battery level to d4
                tag.IRtemperature.disable()
                tag.humidity.disable()
                tag.barometer.disable()
                tag.battery.disable()
                tag.lightmeter.disable()
            except bluepy.btle.BTLEDisconnectError as e:
                # センサーの読み取りエラー発生
                get_data['d1'] = 0
                get_data['d2'] = 0
                get_data['d3'] = 0
                get_data['d5'] = 0
                get_data['d4'] = 0
                print("Error sensorTag disconnect! {0}".format(d))             
    def run(self):
        while True:
            if len(self.data) == 0:
                self.scan()
            else:
                self.get_data()
            time.sleep(10.0)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i',action='store',type=float, default=120.0, help='scan interval')
    parser.add_argument('-t',action='store',type=float, default=5.0, help='scan time out')

    arg = parser.parse_args(sys.argv[1:])
    print("{} {}".format(arg.i,arg.t))
    sensor_obj = sensor_control(arg.i,arg.t)
    ambient_obj = ambient_control()
    sensor_obj.start()
    ambient_obj.start()
    sensor_obj.join()
    ambient_obj.join()


    server_address = ("", 80)
    handler_class = http.server.CGIHTTPRequestHandler #1 ハンドラを設定
    server = http.server.HTTPServer(server_address, handler_class)
    server.serve_forever()

if __name__ == "__main__":
    main()
    while True:
        time.sleep(60.0)
