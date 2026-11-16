"""OCI 오사카 Always Free ARM(A1.Flex) 인스턴스 자동 확보 스크립트 (GitHub Actions용).

모든 민감 정보는 GitHub Secrets → 환경 변수로만 받습니다.
"""
import os
import base64
import random
import re
import sys
import time

import oci

REGION = os.environ.get("OCI_REGION", "ap-osaka-1")
TENANCY = os.environ["OCI_TENANCY"].strip().removeprefix("tenancy=").strip()
COMPARTMENT = os.environ.get("OCI_COMPARTMENT") or TENANCY
SUBNET_NAME = os.environ.get("SUBNET_NAME", "public subnet-busanfood-vcn")
OCPUS = float(os.environ.get("OCPUS", "2"))        # 2026-06 이후 무료 한도: 2 OCPU / 12GB
MEMORY_GB = float(os.environ.get("MEMORY_GB", "12"))
BOOT_GB = int(os.environ.get("BOOT_GB", "50"))
ATTEMPTS = int(os.environ.get("ATTEMPTS", "8"))     # 한 번 실행에서 시도할 횟수
INTERVAL = int(os.environ.get("INTERVAL", "60"))    # 시도 간격(초)
SHAPE = "VM.Standard.A1.Flex"


def normalize_pem(raw):
    """붙여넣기 과정에서 깨진 PEM(줄바꿈·공백·CR·패딩)을 정리합니다. 키 내용은 출력하지 않습니다."""
    raw = raw.replace("\\n", "\n").replace("\r", "")
    blocks = re.findall(r"-----BEGIN ([A-Z ]+)-----(.*?)-----END \1-----", raw, re.S)
    print(f"[key] BEGIN 블록 수={len(blocks)} 종류={[b[0] for b in blocks]}", flush=True)
    priv = [b for b in blocks if "PRIVATE" in b[0]]
    if not priv:
        return raw
    kind, body = priv[0]
    body = re.sub(r"[^A-Za-z0-9+/=]", "", body)
    core = body.rstrip("=")
    if "=" in core:
        print(f"[key] 본문 중간에 '=' {core.count('=')}개 — 제거 후 재시도", flush=True)
        core = core.replace("=", "")
    core += "=" * (-len(core) % 4)
    try:
        der = base64.b64decode(core)
        if len(der) > 4 and der[0] == 0x30 and der[1] == 0x82:
            declared = 4 + (der[2] << 8 | der[3])
            print(f"[key] 디코딩 {len(der)}바이트 / 선언 길이 {declared}바이트", flush=True)
            if len(der) > declared:
                der = der[:declared]
                core = base64.b64encode(der).decode()
                print("[key] 끝에 붙은 여분 데이터 제거", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[key] base64 디코딩 실패: {type(e).__name__}", flush=True)
    lines = "\n".join(core[i:i + 64] for i in range(0, len(core), 64))
    print(f"[key] {kind} 본문 길이={len(core)}", flush=True)
    return f"-----BEGIN {kind}-----\n{lines}\n-----END {kind}-----\n"

config = {
    "user": os.environ["OCI_USER"].strip().removeprefix("user=").strip(),
    "tenancy": TENANCY,
    "fingerprint": os.environ["OCI_FINGERPRINT"].strip().removeprefix("fingerprint=").strip(),
    "key_content": normalize_pem(os.environ["OCI_KEY"]),
    "region": REGION,
}
oci.config.validate_config(config)
compute = oci.core.ComputeClient(config)
network = oci.core.VirtualNetworkClient(config)
identity = oci.identity.IdentityClient(config)


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def existing_a1():
    items = oci.pagination.list_call_get_all_results(
        compute.list_instances, compartment_id=COMPARTMENT
    ).data
    return [i for i in items if i.shape == SHAPE and i.lifecycle_state != "TERMINATED"]


def write_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"{key}={value}\n")


def main():
    found = existing_a1()
    if found:
        log(f"A1 인스턴스가 이미 있습니다: {found[0].display_name} ({found[0].lifecycle_state})")
        write_output("created", "already")
        return 0

    ad = identity.list_availability_domains(TENANCY).data[0].name
    subnets = network.list_subnets(compartment_id=COMPARTMENT, display_name=SUBNET_NAME).data
    if not subnets:
        log(f"서브넷 '{SUBNET_NAME}'을 찾지 못했습니다.")
        return 1
    images = compute.list_images(
        compartment_id=COMPARTMENT,
        operating_system="Canonical Ubuntu",
        operating_system_version="24.04",
        shape=SHAPE,
        sort_by="TIMECREATED",
        sort_order="DESC",
    ).data
    if not images:
        log("Ubuntu 24.04 ARM 이미지를 찾지 못했습니다.")
        return 1
    log(f"AD={ad} / subnet={subnets[0].display_name} / image={images[0].display_name}")

    details = oci.core.models.LaunchInstanceDetails(
        compartment_id=COMPARTMENT,
        availability_domain=ad,
        display_name="osaka-a1",
        shape=SHAPE,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=OCPUS, memory_in_gbs=MEMORY_GB
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            image_id=images[0].id, boot_volume_size_in_gbs=BOOT_GB
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=subnets[0].id, assign_public_ip=True
        ),
        metadata={"ssh_authorized_keys": os.environ["SSH_PUBLIC_KEY"].strip()},
    )

    for n in range(1, ATTEMPTS + 1):
        try:
            inst = compute.launch_instance(details).data
            log(f"#{n} 성공! {inst.display_name} / {inst.id}")
            write_output("created", "yes")
            return 0
        except oci.exceptions.ServiceError as e:
            msg = f"{e.status} {e.code}: {e.message}"
            if "capacity" in (e.message or "").lower():
                wait, why = INTERVAL + random.randint(0, 20), "용량 부족"
            elif e.status == 429:
                wait, why = INTERVAL * 3, "요청 과다(429)"
            elif e.status >= 500:
                wait, why = INTERVAL * 2, f"일시 오류({e.code})"
            else:
                log(f"#{n} 예상 밖 오류 — 중단: {msg}")
                return 1
            if n < ATTEMPTS:
                log(f"#{n} {why} → {wait}s 후 재시도")
                time.sleep(wait)
            else:
                log(f"#{n} {why} — 이번 실행 종료(다음 예약 실행에서 계속)")
    write_output("created", "no")
    return 0


if __name__ == "__main__":
    sys.exit(main())
