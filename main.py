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

RESCAN_TIME = 1200
global_lock = threading.Lock() 
global_rescan_flag = False
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
    self.refresh        : sensor Tag refresh要求(Trueあり Falseなし)　Sensor Tagアクセスエラー時にフラグを立てる
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
        self.refresh = False
        self.started = threading.Event()
        self.data    = {"d1":0,"d2":0,"d3":0,"d4":0,"d5":0}
        self.alive   = True
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

        self.mutex.acquire()
        info = {"rssi":self.device.rssi,"addr":self.device.addr}
        self.mutex.release() 
        return info
    def get_sensor(self):
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
            #self.data = {"d1":0,"d2":0,"d3":0,"d4":0,"d5":0}
            self.alive = False
            global_rescan_flag = True
            self.tag.disconnect()
        
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
            self.mutex.acquire()
            if self.ambient == 0:
                self.setup_ambient()
            else:
                if self.get_sensor() == False:
                    self.refresh = True
                    print("Error sensor access failed")
                time.sleep(3)
                self.send_ambient()
            self.mutex.release()  
            time.sleep(self.time)  


class sensor_control(threading.Thread):

    def __init__(self,measure_interval = 120,scan_interval = 300.0,time_out = 5.0,redis_obj = None):
        self.measure_interval = measure_interval
        self.scan_interval = scan_interval
        self.time_out = time_out
        self.scanner = bluepy.btle.Scanner(0)   # bluepyのScannerインスタンスを生成
        self.redis = redis_obj
        self.sensor = []
        super(sensor_control, self).__init__()
        self.start()
    def __del__(self):
        self.kill()
        for obj in self.sensor:
            del obj
    def append_sensor_list(self,find_sensor_list):
        # Scan結果を元に登録処理を行う
        for sensor in find_sensor_list:
            is_new_device = True
            idx = 0
            
            for i in range(0,len(self.sensor)):
                found_sensor = self.sensor[i]
                if ( sensor.addr == found_sensor.device.addr):
                    if found_sensor.refresh :
                        print("Dell object{}".format(sensor.addr))
                        del found_sensor                                # 異常状態になったので制御オブジェクトを削除して作り直す.
                        del self.sensor[i]                              # オブジェクトの登録を削除する
                    else:
                        print("Sensor {} is already registered.".format(sensor.addr))
                        is_new_device = False
                    break
            if is_new_device :
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

    def scan(self):
        """sensor Tagを見つける
        見つけたデバイスに対応するSensorTag_Accessを作成し、オブジェクトを内部で保持する
        見つけたデバイスがすでに登録済みの場合は、登録処理を行わない.ただし
        """
        find_sensor_list = []
        print('scanning tag...')
        devices = self.scanner.scan(self.time_out)                  # BLEをスキャンする
        for d in devices:
            for (sdid, desc, val) in d.getScanData():
                if sdid == 9 and val == 'CC2650 SensorTag':         # ローカルネームが'CC2650 SensorTag'のものを探す
                    find_sensor_list.append(d)
        self.append_sensor_list(find_sensor_list)
    def refresh_sensor(self):
        time.sleep(600)    
    def delete_sensorTag_obj(self):
        """sensor Tagとの通信エラーが発生した場合は再スキャンを行う
        その際に既存Sensor Tagオブジェクトが全て置き換わってしまうので、登録済みセンサーを全て消去する.
        """
        for dev in self.sensor:
            del dev
        self.sensor = []
    def run(self):
        counter = 0
        """Sensorを見つける
        一つも見つけてない時は20秒おきにスキャンを実行
        一つ以上見つけている時任意の時間(初期値5分)置きに不正終了したオブジェクトが無いかスキャンを実行
        不正終了オブジェクトを見つけた場合は、登録オブジェクトを全て削除する
        """
        while True:
            try:
                if(len(self.sensor) == 0):
                    self.scan()
                    counter = 0
                    time.sleep(20)
                else:
                    time.sleep(self.scan_interval)
                    for dev in self.sensor:
                        if (global_rescan_flag) or (dev.refresh) or (counter > (RESCAN_TIME / self.scan_interval)):
                            self.delete_sensorTag_obj()
                            global_rescan_flag = False
                            break
                    counter += 1
            except:
                print("Error scan")
                self.delete_sensorTag_obj()
                continue


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

    sensor_obj = sensor_control(arg.i,arg.r,arg.t,redis_server)
    server_address = ("", 80)
    handler_class = http.server.CGIHTTPRequestHandler #1 ハンドラを設定
    server = http.server.HTTPServer(server_address, handler_class)
    server.serve_forever()
    sensor_obj.join()

if __name__ == "__main__":
    main()
    while True:
        time.sleep(60.0)
