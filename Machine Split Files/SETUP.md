# Machine Split Setup

Each person runs only their own services. All machines must be on the same network (e.g., university WiFi).

## Machine Assignment

| Machine | Person | Services |
|---------|--------|----------|
| A | Person 1 | Kong, RabbitMQ, Redis, Frontend |
| B | Person 2 | notification-wrapper, telegram-wrapper, twilio-wrapper |
| C | Person 3 | patient-service, appointment-service, book-appointment, consultation-service, start-consultation |
| D | Person 4 | doctor-service, complete-consultation, mc-service, inventory-service, amazon-s3-wrapper, openai-wrapper |
| E | Person 5 | delivery-service, rider-service, assign-delivery, complete-delivery, payment services, tracking, distance-matrix-wrapper |

> **Note on Kong**: Only Person 1 (Machine A) runs Kong. The `kong-machine-split.yml` is only needed on Machine A — no other machine requires it.

---

## Step 1 – Find your IP

Run `ipconfig` (Windows) or `ip addr` (Linux/Mac) and note your **WiFi adapter IPv4 address** (e.g., `192.168.1.12`). Share all 5 IPs with the group.

## Step 2 – Set up your project root

Each person does the following **in their project root**:

**a) Copy your machine's compose file to the project root and rename it:**

```bash
# Example for Person 3 (Machine C):
cp "Machine Split Files/machine-C-compose.yml" docker-compose.yml
```

**b) Copy the env template and fill in all 5 IPs:**

```bash
cp "Machine Split Files/.env.template" .env
# Open .env and replace each 192.168.X.X with the real IP
# Set Google Maps key in backend/distance-matrix-wrapper/.env:
# GOOGLE_MAPS_API_KEY=your_key_here
```

**c) Person 1 only — copy the Kong config to the project root:**

```bash
cp "Machine Split Files/kong-machine-split.yml" kong-machine-split.yml
```

## Step 3 – Open firewall ports

Each person allows incoming TCP traffic on the ports their services expose.
Run in **PowerShell as Administrator**:

```powershell
netsh advfirewall firewall add rule name="mediconnect-XXXX" dir=in action=allow protocol=TCP localport=XXXX
```

| Machine | Ports to open |
|---------|--------------|
| A | 8000, 8001, 5673, 15673, 6379, 8080 |
| B | 5011, 5012, 5020 |
| C | 5030, 5032, 5004, 5033, 5013 |
| D | 5031, 5014, 5010, 5005, 5022, 5021 |
| E | 5000, 5001, 5002, 5004, 5050, 5060, 5061, 5062, 5063 |

## Step 4 – Start your services

From the **project root** (same command for everyone):

```bash
docker compose up --build -d
```

> **Start order**: Machine A (RabbitMQ/Redis) should be running before Machines B–E start.

## Step 5 – Access the site

Open `http://<MACHINE_A_IP>:8080` in your browser.

## Stopping services

```bash
docker compose down
```

---

## Port conflict note (Machine E)

`payment_atomic` and `delivery-service` both use internal port 5000; `payment_wrapper` and `rider-service` both use internal port 5001. They are remapped to different host ports to avoid conflicts:

| Service | Internal port | Host port |
|---------|--------------|-----------|
| delivery-service | 5000 | 5000 |
| rider-service | 5001 | 5001 |
| payment_atomic | 5000 | **5060** |
| payment_wrapper | 5001 | **5061** |
| process_payment | 5002 | **5062** |
| distance-matrix-wrapper | 5063 | **5063** |

`kong-machine-split.yml` already points to these remapped ports.
