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
from bluepy.btle import Scanner, DefaultDelegate

RESCAN_TIME = 1200
global_lock = threading.Lock() 


def reset_bluetooth_driver():
    subprocess.call(['hciconfig', 'hci0', 'down'])
    time.sleep(2)
    subprocess.call(['hciconfig', 'hci0', 'up'])
    time.sleep(2)

class SensorTag_Access(threading.Thread):
    """
    Sensor Tagに周期的に通信を行い、測定値を取得する
    取得した結果をambientサイトに送信する

    Parameters
    ----------
    self.device         : sensor Tag BLEオブジェクト
    self.tag            : sensor Tag classオブジェクト
    self.redis          : redisデータベース制御オブジェクト
    self.ambient        : ambient アクセス用オブジェクト
    self.time           : 周期処理の時間設定
    self.data           : sensor Tag latest data
    self.mutex          : 資源アクセス
    self.started        : スレッドイベントオブジェクト
    """
    def __init__(self,init_data):
        """
        初期化
        Parameters
        ----------
        init_data : list
            "device"  BLEを操作するオブジェクト
            "tag"     Sensor Tag操作オブジェクト
            "redis"   redisデータベース制御オブジェクト
        """
        super(SensorTag_Access, self).__init__()
        self.mutex   = threading.Lock() 
        self.device  = init_data["device"]
        self.tag     = init_data["tag"]
        self.redis   = init_data["redis"]
        self.time    = init_data["time"]
        self.ambient = 0
        self.started = threading.Event()
        self.data    = {"d1":0,"d2":0,"d3":0,"d4":0,"d5":0}
        self.alive   = True
        self.response = False
        self.start()
    def __del__(self):
        """ディストラクター. スレッドを終了させる
        """
        self.kill()
        self.tag.disconnect()
        del self.tag
    
    ### スレッド制御用メソッド
    def begin(self):
        self.started.set()

    def end(self):
        self.started.clear()
    def kill(self):
        self.started.set()
        self.alive = False
        self.join()
    ### スレッド制御用メソッド終わり.

    def get_sensor_info(self):
        """
        Sensor Tagのアドレスを返す。
        呼び元はこのメソッドを利用してSensor Tagが登録済みか否かを判断する.

        Return
        ----------
        hash : addr : Sensor Tagの物理アドレス , rssi : 電波強度
        """    

        info = {"rssi":self.device.rssi,"addr":self.device.addr}
        return info
    def get_sensor(self):

        global global_lock
        """ センサーの値を取得する
        
        Returns:
            [Boolean] -- True : センサー値取得成功. False : センサー値取得失敗. 
        """
        result = False
        global_lock.acquire()
        try:
            self.tag.IRtemperature.enable()
            self.tag.humidity.enable()
            self.tag.barometer.enable()
            self.tag.battery.enable()
            self.tag.lightmeter.enable()
            time.sleep(2.0)
            """
            Parameter:
                d1 : ambient temperature
                d2 : humidity
                d3 : barometer
                d4 : light
                d5 : battery level
            """
            self.data = {
                'd1':self.tag.humidity.read()[0], 
                'd2':self.tag.humidity.read()[1], 
                'd3':self.tag.barometer.read()[1], 
                'd5':self.tag.lightmeter.read(),
                'd4':self.tag.battery.read()
            }
            self.tag.IRtemperature.disable()
            self.tag.humidity.disable()
            self.tag.barometer.disable()
            self.tag.battery.disable()
            self.tag.lightmeter.disable()
            result = True
        except:
            print("ambient get_sensor error dev.addr {}".format(self.device.addr))
        
        global_lock.release()
        return result
    def get_ambient_parameter(self):
        """Ambientへ送信するために必要なパラメータを取得する
        REDISサーバーにアクセスし、sensortagのアドレスに紐づいているパラメータを取得
        データをパースしてsetup_ambientへ渡す.
        Returns:
            [dict] -- [channel ID , Write Key]
        """
        key_data = self.redis.hgetall(self.device.addr)
        list_data = dict([(k.decode('utf-8'), v.decode('utf-8')) for k, v in key_data.items()])
        write_key = 0
        ch_id = 0
        if "write_key" in list_data.keys():
            write_key = list_data["write_key"]
        if "channelId" in list_data.keys():
            ch_id = list_data["channelId"]
        return {"channelId":ch_id,"write_key":write_key}

    def setup_ambient(self):
        """Ambientサーバーへの通信オブジェクトのセットアップを行う
        """
        param = self.get_ambient_parameter()
        print("ambient setup_ambient {}".format(param))
        if not (param["write_key"] == 0 and param["channelId"] == 0) :
            self.ambient = ambient.Ambient(param["channelId"], param["write_key"])
        else:
            print("Ambient Parameter Not found")

    def send_ambient(self):
        print("send ambient dev.addr {0} data = {1}".format(self.device.addr,self.data))
        self.ambient.send(self.data)

    def run(self):
        """
        周期処理スレッド
        Sensor Tagから測定値を取得する
        Ambientへデータを送信する
        """  
        while self.alive:
            self.response = False
            if self.ambient == 0:
                self.setup_ambient()
            else:
                if self.get_sensor() == False:
                    self.response = True
                    print("error sensor access.")
                    while True: time.sleep(1000)
                else:
                    self.send_ambient()
            time.sleep(self.time)  

class sensor_scan(DefaultDelegate):
    def __init__(self,measure_interval = 120,redis_obj = None):
        self.measure_interval = measure_interval    # sensor tag通信間隔
        self.redis = redis_obj                      # redis serverオブジェクを渡す
        self.sensor = []                            # sensor tag通信オブジェクト格納配列
        self.reset = False
        self.found_device_list = []                 # scanで見つけたセンサーリスト.未登録
        self.ambient_alive_counter = 0
    def __del__(self):
        self.del_sensor_access_obj()
        self.kill()
    def del_sensor_access_obj(self):
        """sensor Tagとの通信エラーが発生した場合は再スキャンを行う
        その際に既存Sensor Tagオブジェクトが全て置き換わってしまうので、登録済みセンサーを全て消去する.
        """
        for dev in self.sensor:
            print("disconnect sensor tag")
            dev.tag.disconnect()
            del dev
        self.sensor = []
    def check_ambient_alive(self):
        """sensor tagにアクセス中に応答不能になる場合があるので、周期的に生存チェクを行う
        エラーの場合はbluetooth driverを再起動する.
        """
        is_reset = False
        for obj in self.sensor:
            if obj.response :
                is_reset = True
        if is_reset:
            self.ambient_alive_counter += 1
        else:
            for obj in self.sensor:
                obj.response = False
            self.ambient_alive_counter = 0
 
        if self.ambient_alive_counter > 10:
            self.del_sensor_access_obj()
            reset_bluetooth_driver()

    def register_sensor(self,sensor):
        # Scan結果を元に登録処理を行う  
        print("{} {} ".format(sensor,sensor.addr))
        self.redis.hset(sensor.addr, 'rssi', sensor.rssi)
        init_data = {
            "device":sensor,
            "tag":bluepy.sensortag.SensorTag(sensor.addr),
            "redis":self.redis,
            "time":self.measure_interval
        }
        new_obj = SensorTag_Access(init_data)
        print('Append new SensorTag device addr = %s' % sensor.addr)
        self.sensor.append(new_obj)
    def handleDiscovery(self, d, isNewDev, isNewData):
        """sensorを見つける
        見つけたデバイスに対応するSensor_Accessを作成し、オブジェクトを内部で保持する
        見つけたデバイスがすでに登録済みの場合は、登録処理を行わない.ただし
        """
        if isNewDev == False:
            print("Device already found {}".format(d.addr))
            return
        for (sdid, desc, val) in d.getScanData():
            if sdid == 9 and val == 'CC2650 SensorTag':         # ローカルネームが'CC2650 SensorTag'のものを探す
                self.found_device_list.append(d)

class sensor_control(threading.Thread):

    def __init__(self,measure_interval = 120.0,time_out = 30.0,redis_obj = None):
        self.time_out = time_out
        self.delegate = sensor_scan(measure_interval,redis_obj)
        self.scanner = Scanner().withDelegate(self.delegate )
        super(sensor_control, self).__init__()
        self.start()
    def scan(self):
        """センサー探索。エラーになりやすいのでエラー時には見つけたセンサーを削除する
        """
        try:
            self.scanner.scan(self.time_out)
        except Exception as e:
            print('error!', e)
            self.delegate.found_device_list = []
    def register_sensor(self):
        try:
            for d in self.delegate.found_device_list:
                self.delegate.register_sensor(d)
        except Exception as e:
            print('error!', e)
            self.delegate.found_device_list = []
            for dev in self.delegate.sensor:
                del dev
    def run(self):
        while True:
            if( len(self.delegate.sensor) == 0) :
                self.scan()
                self.register_sensor()
            elif (self.delegate.reset):
                self.delegate.del_sensor_access_obj()
            else:
                self.delegate.check_ambient_alive()

            
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i',action='store',type=float, default=120.0, help='measure interval')
    parser.add_argument('-t',action='store',type=float, default=60.0, help='scan time out')
    parser.add_argument('-r',action='store',type=float, default=10.0, help='scan interval')
    arg = parser.parse_args(sys.argv[1:])
    print("-i {} -t {} -r {}".format(arg.i,arg.t,arg.r))

    # データーベースサーバーの立ち上げ
    redis_server = redis.Redis(host='localhost', port=6379, db=0)   # NoSQLのデータベースライブラリ
    #redis_server.flushdb()                                          # データーベース db 0の中身を消去

    sensor_obj = sensor_control(arg.i,arg.t,redis_server)
    server_address = ("", 80)
    handler_class = http.server.CGIHTTPRequestHandler #1 ハンドラを設定
    server = http.server.HTTPServer(server_address, handler_class)
    server.serve_forever()
    sensor_obj.join()

if __name__ == "__main__":
    main()
    while True:
        time.sleep(60.0)

