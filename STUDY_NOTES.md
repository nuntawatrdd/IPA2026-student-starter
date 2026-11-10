# IPA2026 — สรุปความรู้และคำอธิบายระบบ (สำหรับเตรียมสอบ)

## 0. ใครทำอะไร (ตรงไปตรงมา)

- โค้ด Part 1–4 ใน `app/` และ `ansible/` อยู่ใน commit `79ed506` ("Complet part 1/2") **ก่อนที่ Claude จะเริ่มงานรอบนี้** Claude อ่านโค้ด ไม่ได้เขียน Part 1–4 ใหม่
- สิ่งที่ Claude ทำในรอบนี้:
  1. รัน tests ใน Docker (Python 3.15 ในเครื่องลง PyYAML ไม่ได้) → 91 ผ่าน
  2. build image และรัน `scripts/check_webhook_image.py` → ผ่าน, ตรวจ Student CI → ผ่าน
  3. เปิด bot ใน Docker (`docker compose up -d webhook`, พอร์ต 8001), ตรวจ token ด้วย `GET /people/me`
  4. ลงทะเบียน/อัปเดต Webex webhook ทั้งสองห้องให้ชี้ URL ของ tunnel
  5. สร้างไฟล์ YAML ทดสอบใน `my-live/` (ยังไม่ commit)
  6. **แก้บั๊กจริง 1 จุด** ใน `app/backends/netmiko_textfsm.py` (commit `bd105c5`) — อธิบายใน §6
- จึงต้องเข้าใจโค้ดทั้งระบบ ไม่ใช่แค่ส่วนที่ Claude แก้

## 1. ระบบนี้คืออะไร

Webex bot ที่ทำหน้าที่เป็น "ตัวกลางสั่ง router ด้วย YAML" (Network Automation / ChatOps)

1. คนส่งข้อความใน Webex: @mention bot + แนบไฟล์ YAML หนึ่งไฟล์
2. Webex ส่ง **webhook** (HTTP POST) มาหา bot เป็น JSON ที่มีแค่ **message id** ไม่มีเนื้อ YAML
3. bot ตอบ HTTP 200 ทันที (รับทราบ) แล้วทำงานต่อใน background
4. bot เรียก Webex API ดึงข้อความและดาวน์โหลดไฟล์ YAML
5. ตรวจรูปแบบ → เลือก backend ตาม `method` → คุยกับ router → ได้ผล
6. bot `POST /v1/messages` ส่ง JSON ผลลัพธ์กลับห้องเดิม

เหตุที่ต้องมี tunnel: Webex อยู่บนอินเทอร์เน็ต ส่ง webhook เข้าเครื่องเราที่อยู่หลัง NAT ไม่ได้ จึงใช้ Cloudflare Tunnel เปิด URL HTTPS สาธารณะที่ forward เข้า `127.0.0.1:8001`

## 2. รูปแบบ YAML (หนึ่งไฟล์ = หนึ่งคำขอ)

```yaml
version: 1
router: 10.0.29.103
method: restconf          # restconf | netconf | ansible | netmiko-textfsm
action: apply             # status | plan | apply | delete
desired:
  interface:
    name: Loopback67070088
    ipv4: 10.67.70.88/32
    description: IPA2026-67070088
    admin_state: up       # up | down
```

ตาราง method × action ที่ทำได้ (อยู่ใน `core.py` เป็น `VALID_ACTIONS_BY_METHOD`):

| method | status | plan | apply | delete |
|---|:-:|:-:|:-:|:-:|
| restconf | ✓ | ✓ | ✓ | |
| netconf | ✓ | ✓ | ✓ | |
| ansible | | | ✓ | |
| netmiko-textfsm | ✓ | | | ✓ |

- `status` / `plan` = อ่านอย่างเดียว ไม่แก้ router
- `apply` = ตั้งค่าจริง (สร้างหรือแทนที่ทั้ง interface)
- `delete` = ลบ interface จริง

## 3. ลำดับตรวจคำขอ และรหัส error (Part 1)

ไฟล์ `app/core.py` (`validate_request`) ตรวจ **ตามลำดับตายตัว** แล้วหยุดที่ error แรก:

| ขั้น | ตรวจอะไร | error |
|---|---|---|
| (envelope) | mention bot จริง | `missing_bot_mention` |
| (envelope) | มีไฟล์แนบ | `no_yaml` |
| (webhook) | แนบมากกว่า 1 ไฟล์ | `multiple_attachments` |
| อ่านไฟล์ | parse YAML ไม่ได้ / ไม่ใช่ dict | `invalid_yaml` |
| 1 | `version` ต้องเป็น int `1` (ไม่รับ `"1"`, `1.0`, `true`) | `invalid_version` |
| 2 | `router` ขาด / ไม่อยู่ใน 10.0.29.101–105 | `missing_router` / `invalid_router` |
| 3 | `method` | `missing_method` / `invalid_method` |
| 4 | `action` ไม่รองรับกับ method นั้น | `missing_action` / `invalid_action` |
| 5 | `desired`, `desired.interface`, `.name` | `missing_desired` / `missing_interface` / `missing_interface_name` |
| 6 | เฉพาะ `plan`/`apply`: ipv4, description, admin_state | `missing_ipv4` / `missing_description` / `missing_admin_state` / `invalid_admin_state` |

ผ่านหมด → `{"status":"ok","result":"accepted"}` (ภายในเท่านั้น แล้วไป dispatch)

รหัส error จาก backend: `connection_failed` (ต่อไม่ได้/timeout), `authentication_failed` (user/pass ผิด หรือ HTTP 401/403), `backend_failed` (อย่างอื่นที่พัง)

หลักคิด: คำขอที่ไม่ผ่านการตรวจ **ต้องไม่แตะ router** เพราะ `apply`/`delete` เปลี่ยนของจริง

## 4. การวางแผน (Part 2) — `app/planner.py`

`build_plan(desired, current)` เทียบเฉพาะ 3 ฟิลด์ (`MANAGED_FIELDS` = ipv4, description, admin_state):

| สถานการณ์ | operation | changes |
|---|---|---|
| current = None (ไม่มี interface) | `create` | `{field: {"to": ...}}` ครบ 3 ฟิลด์ |
| มี แต่บางฟิลด์ต่าง | `update` | เฉพาะฟิลด์ที่ต่าง `{"from": เดิม, "to": ใหม่}` |
| ตรงทุกฟิลด์ | `no_change` | `{}` |

ไม่ใส่ `name` ใน changes เพราะ name ใช้ระบุเป้าหมาย ไม่ใช่ค่าที่จัดการ

`app/backends/planning.py` เป็นตัวกลาง: เรียก `status` ของ backend ก่อน → `not_found` ⇒ current=None, `found` ⇒ ใช้ interface ที่อ่านได้, error ⇒ ส่ง error ต่อ → แล้วเรียก `build_plan` (RESTCONF และ NETCONF ใช้ร่วมกัน)

## 5. Backend 4 แบบ (Part 3) — ความรู้ทั่วไปที่ออกสอบได้

### 5.1 RESTCONF (`app/backends/restconf.py`)
- โปรโตคอล HTTPS + JSON ตามโมเดล **YANG** (RFC 8040) พอร์ต 443
- Header: `Accept` และ `Content-Type` = `application/yang-data+json`
- อ่าน: `GET https://<router>/restconf/data/ietf-interfaces:interfaces/interface=<ชื่อ>`
  - 200 = พบ, **404 = ไม่พบ** (ไม่ใช่ error), 401/403 = auth ผิด
  - แปลงผล: `ietf-ip:ipv4.address[0].ip` + `netmask` → CIDR ด้วย `IPv4Interface("ip/mask")`; `enabled` true/false → `up`/`down`
- เขียน: `PUT https://<router>/restconf/data/Cisco-IOS-XE-native:native/interface/Loopback=<เลข>`
  - body: `{"Cisco-IOS-XE-native:Loopback": {"name": N, "description": ..., "ip": {"address": {"primary": {"address": ..., "mask": ...}}}}}`
  - `down` เติม `"shutdown": [null]`
  - 200/201/204 = สำเร็จ
  - **PUT = แทนที่ resource ทั้งก้อน** จึง idempotent (ส่งซ้ำได้ผลเท่าเดิม)
- `verify=False` เพราะ router ใช้ self-signed certificate

### 5.2 NETCONF (`app/backends/netconf.py`)
- โปรโตคอลบน **SSH พอร์ต 830**, ข้อความเป็น **XML** (RFC 6241) ใช้ไลบรารี `ncclient`
- อ่าน: `get-config` source=`running` พร้อม **subtree filter** `<interfaces xmlns="urn:ietf:params:xml:ns:yang:ietf-interfaces"><interface><name>...</name></interface></interfaces>` → parse ด้วย `xml.etree.ElementTree` (ต้องระบุ namespace ตอน find)
- เขียน: `edit-config` target=`running` ด้วย `<native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native"><interface><Loopback nc:operation="replace">...`
  - `nc:operation="replace"` = แทนที่ทั้ง node (ความหมายคล้าย PUT)
  - `escape()` กัน XML injection ใน description
- ไม่มีผลเมื่อไม่พบ → `iface_elem is None` ⇒ `not_found`

### 5.3 Ansible (`app/backends/ansible.py` + `ansible/`)
- bot สั่ง `ansible-playbook -i ansible/inventory.ini ansible/apply_interface.yml -e '<JSON extra-vars>'` ผ่าน `subprocess.run` (timeout 30 วินาที)
- inventory: group `routers` มี host `target`; `ansible_connection=ansible.netcommon.network_cli`, `ansible_network_os=cisco.ios.ios` (บอกว่าเป็น CLI ของ IOS)
- playbook ใช้โมดูล `cisco.ios.ios_config`:
  ```yaml
  parents: ["interface {{ interface_name }}"]
  lines: ["description {{ description }}",
          "ip address {{ ipv4_address }} {{ netmask }}",
          "{{ 'shutdown' if (shutdown | bool) else 'no shutdown' }}"]
  ```
  `parents` = เข้า config mode ของ interface ก่อน แล้วค่อยรัน `lines`
- extra-vars ที่ส่ง: router, interface_name, description, ipv4_address, netmask (แปลงจาก /32 → 255.255.255.255), shutdown (bool), ansible_host/user/password
- แปลง exit code/ข้อความ stderr เป็น error: มีคำว่า authentication/permission denied → `authentication_failed`; returncode 4 (host unreachable) / timed out / connection refused → `connection_failed`; อื่นๆ → `backend_failed`
- Ansible เป็น **declarative + idempotent**: ถ้าค่าตรงอยู่แล้วไม่เปลี่ยน (`changed=0`)

### 5.4 Netmiko + TextFSM (`app/backends/netmiko_textfsm.py`)
- Netmiko = SSH เข้า CLI ของอุปกรณ์ (`device_type: cisco_ios`) ส่งคำสั่งเหมือนพิมพ์เอง
- TextFSM + ntc-templates = แปลง output ข้อความของคำสั่ง show เป็น list ของ dict (`use_textfsm=True`)
- `status`: `show ip interface <ชื่อ>` (IP) + `show interfaces description` (description/สถานะ)
- `delete`: ตรวจว่ามีอยู่ → `send_config_set(["no interface <ชื่อ>"])` → ตรวจซ้ำว่าหายแล้ว
  - ถ้าไม่มีตั้งแต่แรก → `not_found` (idempotent: ลบซ้ำไม่ error)
- คำสั่ง IOS ที่ควรจำ: `show ip interface brief`, `show running-config interface Loopback<N>`, `interface Loopback<N>`, `ip address <ip> <mask>`, `description <text>`, `shutdown` / `no shutdown`, `no interface Loopback<N>`

### เทียบ 4 วิธี (ข้อสอบชอบถาม)
| | RESTCONF | NETCONF | Ansible | Netmiko |
|---|---|---|---|---|
| ขนส่ง | HTTPS 443 | SSH 830 | SSH (CLI) | SSH 22 (CLI) |
| รูปแบบข้อมูล | JSON/YANG | XML/YANG | YAML playbook → CLI | ข้อความ CLI |
| โมเดล | โครงสร้าง | โครงสร้าง | CLI + module | CLI + parser |
| คำสั่งหลัก | GET / PUT | get-config / edit-config | ios_config | send_command / send_config_set |

## 6. โค้ดที่ Claude แก้เพิ่ม (commit `bd105c5`)

**อาการ:** ส่ง `09-delete.yaml` ได้ `{"result":"backend_failed"}` ทั้งที่ interface ถูกลบจริง

**สาเหตุ (ไล่จากหลักฐาน):**
1. ดู log/รันเอง → exception เป็น `textfsm.parser.TextFSMError`
2. ส่ง `show ip interface Loopback67070088` แบบ raw ดู → router ตอบ `% Invalid input detected at '^' marker.` เมื่อ interface ไม่มี
3. TextFSM เอาข้อความ error นั้นไป parse กับ template แล้ว **raise exception** (ไม่ได้คืน string) → ตกไป `except Exception` ใน `delete_interface` → `backend_failed`
4. ผลคือ: ลบสำเร็จ แต่ขั้นตรวจซ้ำ ("ตรวจว่าหายแล้ว") พัง และข้อ 10 (ลบซ้ำ) กับ `status` แบบ netmiko ก็พังเหมือนกัน

**วิธีแก้:** เพิ่มฟังก์ชัน

```python
_NO_INTERFACE_MARKERS = ("invalid input", "incomplete command", "does not exist")

def _show_ip_interface(conn, interface_name):
    command = f"show ip interface {interface_name}"
    raw = conn.send_command(command)                      # อ่านข้อความดิบก่อน
    lower = raw.strip().lower() if isinstance(raw, str) else ""
    if not lower or any(m in lower for m in _NO_INTERFACE_MARKERS):
        return []                                         # ไม่มี interface
    return _parse_textfsm_list(conn.send_command(command, use_textfsm=True))
```

แล้วเปลี่ยนทั้ง `_default_netmiko_get` และ `_default_netmiko_delete` ให้เรียกฟังก์ชันนี้ (รวม 3 จุด) บทเรียน: อย่า parse ผลของคำสั่งที่อาจล้มเหลวโดยไม่เช็ก output ดิบก่อน และ error "ไม่พบ" ต้องแยกจาก error จริง

**ยืนยัน:** tests 91 ผ่าน, ทดสอบกับ router จริง: apply → found → deleted → not_found → ลบซ้ำ not_found, Student CI ผ่าน

## 7. ส่วนเชื่อมต่อ (Part 4)

- `app/integration.py` — `process_request(mentioned_people, attachment_path, bot_person_id)`: envelope → อ่าน YAML (`yaml.safe_load`) → `validate_request` → `dispatch_request`
- `app/dispatcher.py` — `if method == ... and action == ...` เรียกฟังก์ชันของ backend ที่ตรงกัน ไม่ตรง → `invalid_action`
- `app/webex_client.py` — `WebexClient`: `get_me`, `get_message`, `download_file`, `send_message` (Bearer token จาก `WEBEX_BOT_TOKEN`, timeout 10 วินาที)
- `app/webhook_server.py`:
  - `GET /health` → `{"status":"ok","result":"healthy"}`
  - `POST /webhook` → ตรวจ Content-Length/JSON → `submit_event` → ตอบ 200 ทันที
  - `ThreadPoolExecutor` **worker เดียว** → ประมวลผลทีละคำขอ ไม่ให้เปลี่ยน router ซ้อนกัน
  - `WebexEventHandler.handle_event`: ข้ามข้อความของ bot เอง (กัน loop), ดึงข้อความ, นับไฟล์แนบ (0 → `no_yaml`, >1 → `multiple_attachments`, 1 → ดาวน์โหลดไฟล์ลง temp dir), เรียก processor, ส่งผลกลับ **`roomId` ของข้อความต้นทาง** (ไม่ฮาร์ดโค้ดห้อง)
- `Dockerfile` (python:3.12-slim, `CMD python -m app.webhook_server`), `compose.yaml` (service `webhook`, healthcheck, env จาก `.env`), `.github/workflows/student-ci.yaml` (pytest → build → `check_webhook_image.py`)

## 8. ค่าตั้งค่าและความปลอดภัย

- `WEBEX_BOT_TOKEN`, `ROUTER_USER`, `ROUTER_PASS` อยู่ใน `.env` (อยู่ใน `.gitignore`) ห้าม commit
- Webex ส่งเฉพาะข้อความที่ mention bot ให้ bot ในห้องกลุ่ม
- ต้อง **เลือก mention จากรายการ** และเว้นวรรคหลังชื่อก่อนพิมพ์คำสั่ง (ที่ติดกันเป็น `IPA2026-Referenceregister` ทำให้ bot ผู้ตรวจอ่านไม่ออก)

## 9. คำสั่งที่ใช้ตลอดงาน (เติมคำสั่งที่หายไป)

```bash
# รัน tests (Python ในเครื่องใช้ไม่ได้ เลยใช้ Docker)
docker run --rm -v "$PWD":/w -w /w python:3.12-slim sh -c "pip install -q -r requirements.txt; python -m pytest -q tests"

docker compose build webhook                  # สร้าง image
python scripts/check_webhook_image.py         # ตรวจ /health และ /webhook ใน image
HOST_PORT=8001 docker compose up -d webhook   # เปิด bot (พอร์ต 8000 มี container อื่นใช้อยู่)
docker compose ps                             # ดูสถานะ / healthy
docker compose logs webhook                   # ดู log
curl http://127.0.0.1:8001/health             # ตรวจ health
cloudflared tunnel --url http://127.0.0.1:8001   # เปิด tunnel ได้ URL ใหม่ทุกครั้ง

# Webex API (ใช้ Bearer token ของ bot)
GET  https://webexapis.com/v1/people/me                       # ตรวจ token/ชื่อ bot
GET  https://webexapis.com/v1/rooms                           # ห้องที่ bot อยู่
POST https://webexapis.com/v1/webhooks   {name,targetUrl,resource:"messages",event:"created",filter:"roomId=..."}
PUT  https://webexapis.com/v1/webhooks/<id>   {name,targetUrl,status}   # เปลี่ยน URL เมื่อ tunnel เปลี่ยน
GET  https://webexapis.com/v1/messages/<id>                   # ดึงข้อความ (มี files, roomId, mentionedPeople)

git add <ไฟล์> && git commit && git push origin main
gh run list / gh run watch <id>               # ดูผล Student CI
```

ถ้าข้อสอบให้เติมคำสั่งที่หายไป ส่วนที่ออกบ่อย: path ของ RESTCONF (`/restconf/data/ietf-interfaces:interfaces/interface=...`), method (GET อ่าน, PUT แทนที่), พอร์ต (443/830/22), `get-config`/`edit-config`, `ios_config` + `parents`/`lines`, `send_command`/`send_config_set`, `no interface ...`, และรหัสตอบ HTTP (200/204 สำเร็จ, 401/403 auth, 404 ไม่พบ)

## 10. ข้อจำกัดที่ควรรู้

- quick tunnel เปลี่ยน URL ทุกครั้งที่รันใหม่ → ต้องอัปเดต webhook ด้วย `PUT /webhooks/<id>`
- `plan` ไม่ตรวจว่า IP ซ้ำกับ interface อื่น → `apply` อาจได้ `backend_failed` ถ้า IP ซ้ำ
- คะแนนจริงมาจาก `grade` (hidden tests 145 กรณี + Live 20 กรณี) ผ่าน public tests แล้วไม่รับประกันว่าผ่านทั้งหมด
