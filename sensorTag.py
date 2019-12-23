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

# writekey受け取ったらambient_controlオブジェクトを生成する方法のが良さそうなので
# グローバル変数を使ったやりとりは暫定対応とする
class ambient_control(threading.Thread):
    def __init__(self):
        super(ambient_control, self).__init__()
    def set_key(self,channel_id,write_key):
        print("no data")
    def send_param(self,data):
        self.send(data)
    def run(self):
        global g_sensor_data
        global global_lock

        while True:
            time.sleep(20.0)
            if len(g_sensor_data) != 0 :
                global_lock.acquire()
                for d in g_sensor_data:
                    print(d)
                    # sensor tagが見つかり、writekeyとchannel idがセットされているならばambientに送信する.
                    if "ambient" in d:
                        print("send ambient {0}".format(d["data"]))
                        d["ambient"].send(d["data"])
                    else:
                        if not (d["write_key"] == 0 and d["channelId"] == 0) : 
                            d["ambient"] = ambient.Ambient(d["channelId"], d["write_key"])
                global_lock.release()



class sensor_control(threading.Thread):

    def __init__(self,interval = 120,time_out = 5.0,redis_obj = None):
        self.interval = interval
        self.time_out = time_out
        self.scanner = bluepy.btle.Scanner(0)   # bluepyのScannerインスタンスを生成
        self.data = []                          # sensor TagのDeviceオブジェクトと読み込みデータを格納する
        self.redis = redis_obj
        super(sensor_control, self).__init__()
        print(self.redis)
    def scan(self):
        global g_sensor_data
        global global_lock

        print('scanning tag...')
        devices = self.scanner.scan(self.time_out)                  # BLEをスキャンする
        for d in devices:
            for (sdid, desc, val) in d.getScanData():
                if sdid == 9 and val == 'CC2650 SensorTag':         # ローカルネームが'CC2650 SensorTag'のものを探す
                    dev = d
                    if self.is_registered(dev.addr) :
                        continue

                    print('found SensorTag, addr = %s' % dev.addr)
                    tag = bluepy.sensortag.SensorTag(dev.addr)      # 見つけたデバイスに接続する
                    # 見つけたデバイスのデータを一つにまとめる
                    self.data.append({
                        "device" : d,
                        "tag" : tag,
                        "data": {'d1':0,'d2':0,'d3':0,'d4':0,'d5':0},
                        "addr":dev.addr,
                        "rssi":dev.rssi,
                        "channelId":0,
                        "write_key":0
                        })
                    global_lock.acquire()
                    g_sensor_data.append(self.data[-1])
                    self.redis.hset(dev.addr, 'rssi', dev.rssi)
                    global_lock.release()
        sys.stdout.flush()
    
    def is_registered(self,addr):
        sensor_found = False
        # 見つけたセンサーがすでに見つかっている物かチェック
        if len(self.data) != 0:
            for registerd_data in self.data:
                if addr in registerd_data.values():                   # mac addressがすでに登録済みである
                    # すでに登録済み
                    print("this device is registered {0}".format(addr))
                    sensor_found = True
        return sensor_found

    def get_data(self):
        '''
        スキャンして見つかったデバイス毎にセンサーの読み取り値をグローバル変数に格納する
        '''
        global g_sensor_data
        global global_lock

        global_lock.acquire()
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
                #get_data['d1'] = tag.IRtemperature.read()[0]  # set ambient temperature to d1
                get_data['d1'] = tag.humidity.read()[0]  # set ambient temperature to d1
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
        global_lock.release()  
    def get_write_key(self):
        '''
        スキャンして見つかったデバイス毎にambientのwritekeyとchannel idを設定する
        値が取得できない場合は処理をスキップする
        '''
        global g_sensor_data
        global global_lock

        global_lock.acquire() 
        # 見つかったデバイスごとにwrite keyをグローバル変数にいれる
        for data in g_sensor_data:
            key_data = self.redis.hgetall(data["addr"])
            list_data = dict([(k.decode('utf-8'), v.decode('utf-8')) for k, v in key_data.items()])
            write_key = 0
            ch_id = 0
            if "write_key" in list_data.keys():
                write_key = list_data["write_key"]
            if "channelId" in list_data.keys():
                ch_id = list_data["channelId"]
            # write keyとchannel idを格納
            data["write_key"] = write_key
            data["channelId"] = ch_id
        global_lock.release()  

    def run(self):
        time.sleep(10.0)
        while True:
            if len(self.data) == 0:
                self.scan()
            else:
                self.get_data()             # 周期的にセンサーの測定値を取得する
                self.get_write_key()        # 周期的にwebuiからの設定イベントを確認する
            time.sleep(10.0)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i',action='store',type=float, default=120.0, help='scan interval')
    parser.add_argument('-t',action='store',type=float, default=5.0, help='scan time out')
    arg = parser.parse_args(sys.argv[1:])
    print("{} {}".format(arg.i,arg.t))

    # データーベースサーバーの立ち上げ
    redis_server = redis.Redis(host='localhost', port=6379, db=0)   # NoSQLのデータベースライブラリ
    redis_server.flushdb()                                          # データーベース db 0の中身を消去

    sensor_obj = sensor_control(arg.i,arg.t,redis_server)
    ambient_obj = ambient_control()
    sensor_obj.start()
    ambient_obj.start()

    server_address = ("", 80)
    handler_class = http.server.CGIHTTPRequestHandler #1 ハンドラを設定
    server = http.server.HTTPServer(server_address, handler_class)
    server.serve_forever()
    sensor_obj.join()
    ambient_obj.join()

if __name__ == "__main__":
    main()
    while True:
        time.sleep(60.0)
