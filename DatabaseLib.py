import os
import datetime
import re
import requests
from dotenv import load_dotenv
from supabase import create_client
from urllib.parse import quote
import WeatherUtils
from storage3.utils import StorageException

load_dotenv()

def get_latest_ptf_id():
    ptf_files = [f for f in os.listdir('.') if f.endswith('.ptf')]
    ptf_files.sort(key=lambda x: os.path.getmtime(os.path.join('.', x)))
    
    if ptf_files:
        latest_ptf = ptf_files[-1]
        
        match = re.search(r"sun_scan_(\d{6})_(\d{4})", latest_ptf)
        if match:
            date_part = match.group(1)
            time_part = match.group(2)
            id = date_part + time_part
            return id
        else:
            return None
    else:
        return None

def upload_images_to_bucket(directory):
    sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
    observation_name = get_latest_ptf_id()
    if observation_name:
        observation_folder = f"{observation_name}"
        image_files = [f for f in os.listdir(directory) if f.endswith('.jpg') or f.endswith('.png')]
        for image_file in image_files:
            file_path = os.path.join(directory, image_file)
            with open(file_path, 'rb') as file:
                response = sb.storage.from_("images").upload(f"{observation_folder}/{image_file}", file)
            #os.remove(file_path)
    else:
        print("No observation name found.")

def download_images_from_folder():
    sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
    folder_name = get_latest_ptf_id()
    bucket_name = "images"
    num_images = 10
    download_dir = "temp_images"
    os.makedirs(download_dir, exist_ok=True)
    for i in range(1, num_images + 1):
        file_name = f"{folder_name}/{folder_name}_{i:02}.jpg"
        destination = os.path.join(download_dir, f"{folder_name}_{i:02}.jpg")
        try:
            with open(destination, 'wb+') as f:
                res = sb.storage.from_(bucket_name).download(file_name)
                f.write(res)
        except StorageException as e:
            print(f"Error al descargar el archivo {file_name}: {e}")
            if e.message == 'Object not found':
                print(f"Archivo no encontrado: {file_name}")
    return download_dir

def insert_image_to_table():
    sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
    observation_id = get_latest_ptf_id()
    supabase_url = os.getenv('SUPABASE_URL')
    for file_name in os.listdir("temp_images"):
        url = f"https://{supabase_url}/storage/v1/object/public/images/{observation_id}/{quote(file_name)}"
        sb.table("images").insert({"observationid": observation_id, "url": url}).execute()
    filelist = [f for f in os.listdir("temp_images")]
    for f in filelist:
        os.remove(os.path.join("temp_images", f))
    os.rmdir("temp_images")

def send_data_to_database():
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_KEY')
    weather_data_json = WeatherUtils.getCurrentWeather()

    current_date = datetime.datetime.now()
    formatted_date = current_date.strftime('%Y-%m-%d')

    data_to_insert = {
        'id': str(get_latest_ptf_id()),
        'weather': weather_data_json,
        'date': formatted_date
    }

    try:
        response = requests.post(
            f"{supabase_url}/rest/v1/testing",
            headers={
                "apikey": supabase_key,
                "Content-Type": "application/json"
            },
            json=data_to_insert
        )
        response.raise_for_status()

        try:
            if response.text:
                return response.json()
            else:
                print("La respuesta está vacía")
                return None
        except requests.exceptions.JSONDecodeError:
            print("Error al decodificar la respuesta JSON")
            print("Contenido de la respuesta:", response.text)
            return None

    except requests.exceptions.RequestException as e:
        print(f"Error en la solicitud HTTP: {e}")
        if e.response and e.response.status_code == 409:
            print("Conflicto: el ID ya existe en la base de datos.")
        return None

"""
def get_latest_cvs_file():
    cvs_files = [f for f in os.listdir('.') if f.endswith('.cvs')]
    cvs_files.sort(key=lambda x: os.path.getmtime(os.path.join('.', x)))
    
    if cvs_files:
        return cvs_files[-1]
    else:
        return None

def upload_latest_cvs_to_bucket():
    latest_cvs = get_latest_cvs_file()
    if not latest_cvs:
        print("No CVS files found.")
        return

    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_KEY')
    sb = create_client(supabase_url, supabase_key)
    
    observation_id = get_latest_ptf_id()
    file_path = latest_cvs
    file_name = os.path.basename(file_path)
    observation_folder = f"{observation_id}"
    
    with open(file_path, 'rb') as file:
        response = sb.storage.from_("logs").upload(f"{observation_folder}/{file_name}", file)
        
    if response.status_code == 200:
        url = f"https://{supabase_url}/storage/v1/object/public/logs/{observation_folder}/{quote(file_name)}"
        sb.table("logs").insert({"observationid": observation_id, "url": url}).execute()
        print("CVS file uploaded and URL inserted successfully.")
    else:
        print(f"Failed to upload CVS file: {response.text}")

def activate_axis_and_send_table():
    acu_cmd.send_TrTable()
    
def get_latest_ptf_file(directory):
    ptf_files = [file for file in os.listdir(directory) if file.endswith('.ptf')]
    if ptf_files:
        return max(ptf_files, key=os.path.getctime)
    else:
        return None

def upload_latest_ptf_file(ptf_directory):
    latest_ptf = get_latest_ptf_file(ptf_directory)
    if latest_ptf:
        subprocess.run(["scp", os.path.join(ptf_directory, latest_ptf), "oper@fs32:/usr2/acu_interface/COMANDC/PTF/"])
        print("Último archivo PTF cargado con éxito:", latest_ptf)
    else:
        print("No se encontraron archivos PTF en el directorio:", ptf_directory)
        
def upload_ptf_file(ptf_file):
    subprocess.run(["scp", ptf_file, "oper@fs32:/usr2/acu_interface/COMANDC/PTF/"])
    
def get_acu_position():
    az, el = acu_par.get_AzEl_pos()
    return az, el
"""

