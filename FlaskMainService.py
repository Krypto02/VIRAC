import socket
import struct
import threading
import queue
import time
import csv
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import json
from WeatherUtils import getCurrentWeather
from AntennaUtils import *
import DatabaseLib
import astropy.units as u
from astropy.io import fits
import numpy as np

app = Flask(__name__)
socketio = SocketIO(app)

BUFFER_SIZE = 2048

buffer_queue = queue.Queue(BUFFER_SIZE)
receiver_thread = None
processor_thread = None
monitor_thread = None
running = False

output_file_prefix = None

rt32_antenna = RT32()
rt32_antenna.set_location(latitude=57.5535171694, longitude=21.8545525000, elevation=20)

def calculate_antenna_positions_f(year, month, day, hour, minute):
    currentWeather = getCurrentWeather()

    path = ''
    temperature = u.Quantity(currentWeather['temperature_2m'], unit=u.deg_C)
    pressure = u.Quantity(currentWeather['surface_pressure'], unit=u.hPa)
    relative_humidity = u.Quantity(currentWeather['relative_humidity_2m'], unit=u.percent)
    obswl = u.Quantity(50000, unit=u.nm)

    weather = Weather(temperature, pressure, relative_humidity, obswl)

    observation = SpiralSunObservation(weather, rt32_antenna, year, month, day, hour, minute)

    az_anten, el_anten, az_sun, el_sun, xx1, yy1, utc = observation.calculatePositions()
    observation.generateFile(path, az_anten, el_anten, utc)

    return True

def buffer_monitor(buffer_queue):
    global running
    while running:
        print(f"Estado del buffer: {buffer_queue.qsize()} elementos")
        time.sleep(5)

def udp_receiver(ip, port, buffer_queue):
    global running
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    try:
        sock.bind((ip, port))
        print(f"Esperando mensajes en {ip}:{port}")
        while running:
            try:
                data, addr = sock.recvfrom(2048)
                if data:
                    buffer_queue.put((data, addr))
                    print(f"Paquete recibido de {addr} y añadido al buffer. Tamaño del buffer: {buffer_queue.qsize()}")
            except Exception as e:
                print(f"Error en udp_receiver: {e}")
    except OSError as e:
        print(f"Error en socket.bind: {e}")
    finally:
        sock.close()

def data_processor(buffer_queue, output_file_prefix):
    global running
    
    combined_data = []
    packet_count = 0

    channels = {
        1: "4.07GHz",
        4: "6.42GHz",
        7: "8.40GHz",
        9: "9.80GHz",
        11: "11.90GHz"
    }
    
    dataFIT = {}

    for c in channels.keys():
        dataFIT.update({f"UTC RCP {c}": [], f"RCP {c} {channels[c]}": [], f"UTC LCP {c}": [], f"LCP {c} {channels[c]}": []})
        
    while running or not buffer_queue.empty():
        if not buffer_queue.empty():
            try:
                data, addr = buffer_queue.get()
                print(f"Procesando paquete de {addr}. Tamaño del buffer después de procesar: {buffer_queue.qsize()}")

                packet_count += 1
                socketio.emit('new_data', {'packet_number': packet_count , "data" : data.decode('utf-8')}, namespace='/')

                header = data[:8].decode('utf-8')
                if header.startswith("LNSPD___"):
                    index = 8   
    
                    for channel in channels.keys():                      
                        index += 4
                        
                        # Leer timestamps y datos en HEX y convertir a decimal
                        t_r_timestamp_hex = data[index:index+8]                        
                        
                        t_r_timestamp = int(t_r_timestamp_hex, 16)
                        index += 8
                        
                        t_r_data_hex = data[index:index+8]
                        t_r_data = int(t_r_data_hex, 16)
                        index += 8
                        
                        t_l_timestamp_hex = data[index:index+8]
                        t_l_timestamp = int(t_l_timestamp_hex, 16)
                        index += 8
                        
                        t_l_data_hex = data[index:index+8]
                        t_l_data = int(t_l_data_hex, 16)
                        index += 8

                        # Agregar los datos de este canal al diccionario dataFIT
                        dataFIT[f"UTC RCP {channel}"].append(t_r_timestamp)
                        dataFIT[f"RCP {channel} {channels[channel]}"].append(t_r_data)
                        dataFIT[f"UTC LCP {channel}"].append(t_l_timestamp)
                        dataFIT[f"LCP {channel} {channels[channel]}"].append(t_l_data)
                    

            except Exception as e:
                print(f"Error en data_processor: {e}")

    if dataFIT:
        try:
            fit_filename = f"{output_file_prefix}_{datetime.now().strftime('%y%m%d_%H%M%S')}.fit"
            
            # Convertir el diccionario en objetos de columna
            col_defs = fits.ColDefs([fits.Column(name=k, format='J', array=v) for k, v in dataFIT.items()])
            
            # Crear el archivo FIT
            hdu = fits.BinTableHDU.from_columns(col_defs)
            hdu.writeto(fit_filename, overwrite=True)
            print(f"FIT file saved as {fit_filename}")
        except Exception as e:
            print(f"Error al escribir archivo FIT: {e}")

@app.route('/stopReceiver', methods=['POST'])
def stop_receiver():
    global running, output_file_prefix
    if running:
        running = False
        if receiver_thread:
            receiver_thread.join()
        if processor_thread:
            processor_thread.join()
        if monitor_thread:
            monitor_thread.join()

        return jsonify({"message": "Receiver stopped and data converted to CSV and FIT files"}), 200
    else:
        return jsonify({"message": "Receiver is not running"}), 200

@app.route('/getCurrentWeather', methods=['POST'])
def getWeatherForecast():
    currentWeather = getCurrentWeather()
    return currentWeather, 200

@app.route('/calculate_antenna_positions', methods=['POST'])
def calculate_antenna_positions():
    data = request.get_json()
    year = data.get('year')
    month = data.get('month')
    day = data.get('day')
    hour = data.get('hour')
    minute = data.get('minute') 

    calculate_antenna_positions_f(year, month, day, hour, minute)
    
    return jsonify({'message': 'Antenna positions calculated successfully'})

@app.route('/sendPTFtoACU', methods=['POST'])
def sendPTFtoACU():
    global running, receiver_thread, processor_thread, monitor_thread, output_file_prefix
    if not running:
        output_file_prefix = f"lnsp4_{datetime.now().strftime('%y%m%d_%H%M%S')}"
        running = True
        receiver_thread = threading.Thread(target=udp_receiver, args=("0.0.0.0", 8888, buffer_queue))
        receiver_thread.daemon = True
        receiver_thread.start()

        processor_thread = threading.Thread(target=data_processor, args=(buffer_queue, output_file_prefix))
        processor_thread.daemon = True
        processor_thread.start()

        monitor_thread = threading.Thread(target=buffer_monitor, args=(buffer_queue,))
        monitor_thread.daemon = True
        monitor_thread.start()

        return jsonify({"message": f"PTF sent to ACU and receiver started, saving to {output_file_prefix}"}), 200
    else:
        return jsonify({"message": "Receiver is already running"}), 200

@app.route('/uploadImages', methods=['POST'])
def upload_images():
    DatabaseLib.send_data_to_database()
    DatabaseLib.upload_images_to_bucket(".")
    DatabaseLib.download_images_from_folder()
    DatabaseLib.insert_image_to_table()
    #DatabaseLib.upload_latest_cvs_to_bucket()
    return jsonify({"message": "Images uploaded successfully"}), 200

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == "__main__":
    socketio.run(app, port=5000)
