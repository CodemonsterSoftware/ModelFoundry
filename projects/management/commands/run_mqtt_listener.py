import time
import json
import ssl
import paho.mqtt.client as mqtt
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import close_old_connections
from projects.models import Machine, Part, UserSettings
import re
import logging
from projects.logging_utils import apply_system_log_level

logger = logging.getLogger('mqtt_listener')

class Command(BaseCommand):
    help = 'Runs the MQTT listener for 3D printer status updates'

    def handle(self, *args, **options):
        apply_system_log_level()
        logger.info('Starting dynamic MQTT listener...')
        
        clients = {} # Map of machine_id -> client
        
        try:
            while True:
                # Fetch all valid machines
                valid_machines = Machine.objects.exclude(ip_address__isnull=True).exclude(ip_address__exact='')
                current_machine_ids = set()
                
                logger.debug(f"Checking for valid machines... found {valid_machines.count()}")
                
                for machine in valid_machines:
                    if machine.ip_address and machine.mqtt_access_code:
                        current_machine_ids.add(machine.id)
                        
                        # Connect if not already connected
                        if machine.id not in clients:
                            logger.info(f"[Machine {machine.id}] Not in clients, setting up...")
                            client = self.setup_mqtt_client(machine)
                            if client:
                                clients[machine.id] = client
                    else:
                        logger.warning(f"[Machine {machine.id}] Missing IP or access code.")
                                
                # Disconnect any clients that are no longer valid machines
                client_ids = list(clients.keys())
                for cid in client_ids:
                    if cid not in current_machine_ids:
                        logger.warning(f"[Machine {cid}] Disconnecting (no longer configured)")
                        clients[cid].loop_stop()
                        clients[cid].disconnect()
                        del clients[cid]
                        
                # Close old DB connections before sleeping
                close_old_connections()
                time.sleep(10)
                
        except KeyboardInterrupt:
            logger.info('Stopping MQTT listener...')
            for cid, client in clients.items():
                client.loop_stop()
                client.disconnect()

    def setup_mqtt_client(self, machine):
        import uuid
        import ssl
        import socket
        import re
        
        # Fetch serial number from SSL certificate
        serial_number = None
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with socket.create_connection((machine.ip_address, 8883), timeout=5) as sock:
                with ctx.wrap_socket(sock, server_hostname=machine.ip_address) as ssock:
                    der = ssock.getpeercert(True)
                    # Use ASN.1 UTF8String tag (\x0c) + length byte to extract exactly the right number of chars
                    for m in re.finditer(b'\x0c(.)(0[0-9A-Z]{12,16})', der):
                        length = m.group(1)[0]
                        string_val = m.group(2)
                        if len(string_val) >= length:
                            serial_number = string_val[:length].decode('utf-8')
                            logger.info(f"[Machine {machine.id}] Discovered serial number: {serial_number}")
                            break
        except Exception as e:
            logger.warning(f"[Machine {machine.id}] Failed to get serial number from cert: {e}")
            
        if not serial_number:
            serial_number = "+"
            
        unique_id = str(uuid.uuid4())[:8]
        client = mqtt.Client(client_id=f"MF_{machine.id}_{unique_id}", protocol=mqtt.MQTTv311)
        
        # Bambu uses TLS but usually with self-signed certs
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
        
        # Username is always 'bblp' for Bambu Studio MQTT
        client.username_pw_set("bblp", machine.mqtt_access_code)
        
        # Store machine id and serial on client for callbacks
        client.machine_id = machine.id
        client.machine_serial = serial_number
        
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        client.on_disconnect = self.on_disconnect
        client.on_subscribe = self.on_subscribe
        
        try:
            logger.info(f"[Machine {machine.id}] Connecting to {machine.name} ({machine.ip_address})...")
            client.connect(machine.ip_address, 8883, 60)
            client.loop_start()
            return client
        except Exception as e:
            logger.error(f"[Machine {machine.id}] Failed to connect to {machine.name}: {e}")
            return None

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"[Machine {client.machine_id}] Connected successfully")
            
            # Update last_seen immediately upon successful connection
            now = timezone.now()
            machine = Machine.objects.filter(id=client.machine_id).first()
            if machine:
                machine.last_seen = now
                machine.save(update_fields=['last_seen'])
                
            topic = f"device/{client.machine_serial}/report"
            logger.debug(f"[Machine {client.machine_id}] Subscribing to exact topic: {topic}")
            client.subscribe(topic)
        else:
            logger.error(f"[Machine {client.machine_id}] Connection failed with code {rc}")
            
        close_old_connections()

    def on_disconnect(self, client, userdata, rc):
        logger.warning(f"[Machine {client.machine_id}] Disconnected with code {rc}")

    def on_subscribe(self, client, userdata, mid, granted_qos):
        logger.debug(f"[Machine {client.machine_id}] Subscribed successfully, mid={mid}, granted_qos={granted_qos}")

    def on_message(self, client, userdata, msg):
        try:
            # Throttle last_seen database updates to max once per minute
            now = timezone.now()
            machine = Machine.objects.filter(id=client.machine_id).first()
            if machine:
                if not machine.last_seen or (now - machine.last_seen).total_seconds() > 60:
                    machine.last_seen = now
                    machine.save(update_fields=['last_seen'])
                    
            raw_payload = msg.payload.decode('utf-8')
            logger.debug(f"[Machine {client.machine_id}] Raw Payload: {raw_payload[:1000]}")
            
            payload = json.loads(raw_payload)
            
            # Bambu print structure
            if "print" in payload:
                print_data = payload["print"]
                
                # Check if we have subtask name (filename without extension)
                if "subtask_name" in print_data:
                    subtask_name = print_data["subtask_name"]
                    
                    # Status logic:
                    # 'RUNNING', 'FINISH', 'FAILED', 'IDLE'
                    status = print_data.get("stg_cur", -1) # Sometimes in stg_cur or gcode_state
                    gcode_state = print_data.get("gcode_state", "UNKNOWN")
                    
                    # Debug log to show we are getting payloads
                    logger.debug(f"[Machine {client.machine_id}] Received payload - File: {subtask_name}, State: {gcode_state}")
                    
                    self.process_print_status(client.machine_id, subtask_name, gcode_state)
        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.error(f"[Machine {client.machine_id}] Error processing message: {e}")
        finally:
            close_old_connections()
            
    def process_print_status(self, machine_id, subtask_name, state):
        # Check if auto_complete is globally enabled by ANY user (in a multi-tenant setup we would check the machine's user)
        # For simplicity, if any user has auto_complete_prints enabled, we allow it.
        auto_complete = False
        try:
            for us in UserSettings.objects.filter(settings_type='api'):
                settings_data = json.loads(us.settings_data) if us.settings_data else {}
                if settings_data.get('auto_complete_prints'):
                    auto_complete = True
                    break
        except Exception:
            pass

        def clean_name(name):
            if not name:
                return ""
            name = re.sub(r'\.(gcode|3mf|stl|obj)$', '', name, flags=re.IGNORECASE)
            name = re.sub(r'[^a-zA-Z0-9]', '', name).lower()
            return name
            
        clean_subtask = clean_name(subtask_name)
        
        # Find matching part
        for part in Part.objects.all():
            # First try matching exact slice filename
            is_match = False
            if part.assigned_slice_filename and clean_name(part.assigned_slice_filename) == clean_subtask:
                is_match = True
            elif clean_name(part.name) == clean_subtask:
                # Fallback to fuzzy part name matching
                is_match = True

            if is_match:
                if state == "RUNNING" and part.print_status != 'printing':
                    part.print_status = 'printing'
                    part.save()
                    logger.info(f"[Machine {machine_id}] Updated {part.name} status to Printing")
                elif state == "FINISH" and part.print_status != 'completed':
                    part.print_status = 'completed'
                    if auto_complete:
                        part.completed += 1
                        logger.info(f"[Machine {machine_id}] Updated {part.name} status to Completed. Count: {part.completed}")
                    else:
                        logger.info(f"[Machine {machine_id}] Updated {part.name} status to Completed. (Auto-increment disabled)")
                    part.save()
                    
                    # Record history
                    from projects.models import PrintHistory
                    PrintHistory.objects.create(
                        user=part.project.user,
                        part=part,
                        machine_id=machine_id,
                        filename=subtask_name,
                        status='completed'
                    )
                elif state in ["FAILED", "CANCEL"] and part.print_status != 'pending':
                    # Reset part status so it can be printed again
                    part.print_status = 'pending'
                    part.save()
                    
                    from projects.models import PrintHistory
                    PrintHistory.objects.create(
                        user=part.project.user,
                        part=part,
                        machine_id=machine_id,
                        filename=subtask_name,
                        status=state.lower()
                    )
                    logger.warning(f"[Machine {machine_id}] Recorded print {state.lower()} for {part.name}")
                break
