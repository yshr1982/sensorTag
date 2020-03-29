# -*- coding: utf-8 -*-
# while True:
#     SensorTagをスキャンし、見つけたデバイスに接続し、
#     温度、湿度、気圧、照度、バッテリーレベルを取得し、Ambientに送信
#
import bluepy
from bluepy.btle import Scanner, DefaultDelegate
import time
import sys
import argparse
import ambient
import threading
import http.server
import redis
import subprocess

RESCAN_TIME = 1200

class Sensor_Access(threading.Thread):
    """
    Sensor Tagに周期的に通信を行い、測定値を取得する
    取得した結果をambientサイトに送信する

    Parameters
    ----------
    self.addr           : sensor mac address
    self.redis          : redisデータベース制御オブジェクト
    self.ambient        : ambient アクセス用オブジェクト
    self.time           : 周期処理の時間設定
    self.data           : sensor latest data
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
            "redis"   redisデータベース制御オブジェクト
        """
        super(Sensor_Access, self).__init__()
        self.mutex   = threading.Lock() 
        self.addr    = init_data["addr"]
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
    def get_ambient_parameter(self):
        """Ambientへ送信するために必要なパラメータを取得する
        REDISサーバーにアクセスし、sensorのアドレスに紐づいているパラメータを取得
        データをパースしてsetup_ambientへ渡す.
        Returns:
            [dict] -- [channel ID , Write Key]
        """
        key_data = self.redis.hgetall(self.addr)
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
    def set_data(self,param):
        """[センサー測定データを更新する]
        ０値が入ったデータは未更新データとして無視する
        Arguments:
            param センサーデータ
        """
        self.mutex.acquire()
        if param["temp"] > 0: self.data["d1"] = param["temp"]
        if param["hum"] > 0: self.data["d2"] = param["hum"]
        if param["batt"] > 0: self.data["d3"] = param["batt"]
        self.mutex.release()  

    def send_ambient(self):
        print("send ambient dev.addr {0} data = {1}".format(self.addr,self.data))
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
                self.send_ambient()
            self.mutex.release()  
            time.sleep(self.time)  


class sensor_control(DefaultDelegate):
    def __init__(self,measure_interval = 120,redis_obj = None):
        self.measure_interval = measure_interval
        self.redis = redis_obj
        self.read_data = []
        self.ambient_obj = []
        
    def is_registered(self,addr):
        """[登録済みセンサーか確認する]
        """
        ret = False
        if len(self.ambient_obj) == 0:
            return False
        for d in self.ambient_obj:
            if d.addr == addr:
                ret = True
        return ret
    def register_ambient(self,addr):
        """[ambientへsensor情報を登録する]
        """
        if self.is_registered(addr):
            return False

        self.redis.hset(addr, 'rssi', 0)
        init_data = {
            "addr":addr,
            "redis":self.redis,
            "time":self.measure_interval
        }
        self.ambient_obj.append(Sensor_Access(init_data)) 
        d = {addr:{"temp":0,"hum":0,"batt":0}}
        if len(self.read_data) == 0:
            self.read_data = d
        else:
            self.read_data.update(d)
        return True    
        
    def set_data(self,addr,param):
        """[Save read sensor data]
        
        Arguments:
            param {[dict]} -- [sensor data array]
            addr : mac address
            temp : temperature
            hum  : humidity
            batt : battery
        """
        print(param)
        if self.is_registered(addr):
            for d in self.ambient_obj:
                if d.addr == addr:
                    print("ambient obj {} : {}".format(d,param))
                    d.set_data(param)


    def handleDiscovery(self, devices, isNewDev, isNewData):
        """sensorを見つける
        見つけたデバイスに対応するSensor_Accessを作成し、オブジェクトを内部で保持する
        見つけたデバイスがすでに登録済みの場合は、登録処理を行わない.ただし
        """
        data = devices.getScanData()
        if "4c:65:a8:dc" in devicesaddr:
            self.register_ambient(devicesaddr)
        if self.is_registered(devicesaddr):
            print("{}/{}".format(devicesaddr,data))
            for (sdid, desc, val) in data:
                if sdid == 22:
                    """[MJ_HT_V1 data format]
                    |Byte 14 val| note       | data position|
                    |===========|============|==============|
                    |0x04       |Temperature | 17 + 18      |
                    |0x06       |Humidity    | 17 + 18      |
                    |0x0A       |Battery     | 17           |
                    |0x0D       |Temperature | temp: 17 + 18|
                    |           |and Humidity| hum: 19 + 20 |
                    """
                    if len(val) <= 14*2:
                        print("data loss")
                        continue
                    subsequent = val[13*2:14*2]
                    print(subsequent)
                    if subsequent == '04':
                        self.read_data[devicesaddr].update(temp=int(val[17*2:18*2] + val[16*2:17*2],16)/10.0)
                    elif subsequent == '06':
                        self.read_data[devicesaddr].update(hum=int(val[17*2:18*2] + val[16*2:17*2],16)/10.0)
                    elif subsequent == '0A' or subsequent == '0a':
                        self.read_data[devicesaddr].update(batt=int(val[16*2:17*2],16)/10.0)
                    elif subsequent == '0D' or subsequent == '0d':
                        self.read_data[devicesaddr].update(temp=int(val[17*2:18*2] + val[16*2:17*2],16)/10.0)
                        self.read_data[devicesaddr].update(hum=int(val[19*2:20*2] + val[18*2:19*2],16)/10.0)
                    else:
                        print("data not match")
                    print(self.read_data)
            self.set_data(devicesaddr,self.read_data[devicesaddr])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i',action='store',type=float, default=120.0, help='measure interval')
    parser.add_argument('-t',action='store',type=float, default=10.0, help='scan time out')
    parser.add_argument('-r',action='store',type=float, default=300.0, help='scan interval')
    arg = parser.parse_args(sys.argv[1:])
    print("-i {} -t {} -r {}".format(arg.i,arg.t,arg.r))

    # データーベースサーバーの立ち上げ
    redis_server = redis.Redis(host='localhost', port=6379, db=0)   # NoSQLのデータベースライブラリ
    #redis_server.flushdb()                                          # データーベース db 0の中身を消去

    delegate = sensor_control(arg.i,redis_server)
    scanner = Scanner().withDelegate(delegate)
    server_address = ("", 80)
    handler_class = http.server.CGIHTTPRequestHandler #1 ハンドラを設定
    server = http.server.HTTPServer(server_address, handler_class)
    while True:
        try:
            for i in range(3):
                scanner.scan(arg.t)
        except Exception as e:
            print('error!', e)
            subprocess.call(['hciconfig', 'hci0', 'down'])
            time.sleep(2)
            subprocess.call(['hciconfig', 'hci0', 'up'])
            time.sleep(2)
    
    server.serve_forever()
    
if __name__ == "__main__":

    main()



