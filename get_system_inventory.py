"""Get system information using the redfish API"""

import os
from multiprocessing import Pool
import requests


REDFISH_URI = "https://{}/redfish/v1/Systems/System.Embedded.1"
idrac_username = os.environ.get("IDRAC_USERNAME", "root")
idrac_password = os.environ.get("IDRAC_PASSWORD", "calvin")

class RedfishError(Exception):
    """Error if it's related to the redfish api"""
    pass

def _make_request(uri):
    """Make request to the redfish API with the given uri"""

    response = requests.get(uri, verify=False, auth=(idrac_username, idrac_password))

    if response.status_code == 401:
        raise RedfishError("Incorrect username/password. Given URI: %s" % uri)

    if response.status_code == 404:
        raise RedfishError("URI not found. Given URI: %s" % uri)

    if response.status_code not in [200, 202]:
        raise RedfishError("Host may not support Redfish API. Given URI: %s" % uri)

    return response.json()


def get_general_information(idrac_ip):
    """Get general system information"""

    irrelevant_devices = ["Xeon", "C610/X99", "C600/X79", "G200eR2", "PCI Bridge"]

    data = _make_request(REDFISH_URI.format(idrac_ip))

    system_model = data["Model"]
    ram = data["MemorySummary"]["TotalSystemMemoryGiB"]
    total_threads = data["ProcessorSummary"]["LogicalProcessorCount"]
    total_cores = total_threads / data["ProcessorSummary"]["Count"] # assuming 2 threads per core.
    cpu_model = data["ProcessorSummary"]["Model"]

    pciedevices = data["PCIeDevices"]
    all_pcie_devices = []
    other_nics = []

    for item in pciedevices:
        data = _make_request("https://" + idrac_ip + item["@odata.id"])

        manufacturer = data.get("Manufacturer", data.get("Id"))
        name = data.get("Name", data.get("Description"))

        if not any(word in name for word in irrelevant_devices):
            all_pcie_devices.append(" ".join([manufacturer, name]))

        if "Solarflare" in manufacturer or "Ethernet" in name:
            other_nics.append(" ".join([manufacturer, name]))

    all_pcie_devices_string = "+".join(all_pcie_devices)

    return {"system_model": system_model,
            "ram": ram,
            "total_threads": total_threads,
            "total_cores": total_cores,
            "cpu_model": cpu_model,
            "other_nics": other_nics,
            "all_pcie_devices": all_pcie_devices_string}


def get_disk_information(idrac_ip):
    """Get disk information"""
    data = _make_request(REDFISH_URI.format(idrac_ip) + "/Storage")

    all_drives = []

    controller_list = []
    for item in data["Members"]:
        controller_list.append(item["@odata.id"])

    for controller in controller_list:
        data = _make_request("https://" + idrac_ip + controller)

        if data["Drives"] == []:
            print("No drives on controller %s" % controller.split("/")[-1])
        else:
            for drive in data["Drives"]:

                drive_data = _make_request("https://" + idrac_ip + drive["@odata.id"])
                drive_name = str(round(drive_data["CapacityBytes"]/(2**30))) + " "  + drive_data["MediaType"]
                all_drives.append(drive_name)

    return all_drives

def get_nic_information(idrac_ip):
    """Get nic information"""
    data = _make_request(REDFISH_URI.format(idrac_ip) + "/NetworkInterfaces")

    network_uri_list = [item['@odata.id'] for item in data["Members"]]

    all_nics = []

    for item in network_uri_list:
        item = item.replace("Interfaces", "Adapters")
        data = _make_request("https://" + idrac_ip + item)

        # apparently some nics don't have a model field
        model = data.get("Model", data.get("Id"))

        all_nics.append(model)

    return all_nics

def get_all(idrac_ip):
    """Get complete system information in a format MOC wanted"""

    print("Getting all info for %s" % idrac_ip)

    try:
        disks = get_disk_information(idrac_ip)
        disks = " + ".join([disk for disk in disks])

        nics = get_nic_information(idrac_ip)

        general = get_general_information(idrac_ip)
        all_information = ",".join([idrac_ip,
                                    general["system_model"],
                                    general["cpu_model"],
                                    str(general["total_cores"]),
                                    str(general["total_threads"]),
                                    str(general["ram"]),
                                    disks,
                                    general["all_pcie_devices"],
                                    *nics,
                                    *general["other_nics"]])
    except requests.exceptions.ConnectionError as err:
        return "Could not reach host %s" % idrac_ip
    except RedfishError as err:
        return str(err)

    return all_information



if __name__ == '__main__':

    kaizen_nodes = []
    for rack in [3, 5, 15, 17, 19]:
        for unit in range(1, 42):
            kaizen_nodes.append("10.0.{}.{}".format(rack, unit))

    kumo_nodes = ["10.0.23." + str(i) for i in range(101,117)] + ["10.1.10." + str(i) for i in range(1,17)]
    kumo_nodes.append("10.0.23.11") # kumo storage node

    all_nodes = kaizen_nodes + kumo_nodes
    print(len(all_nodes))

    # I should try to use async instead of brute forcing with
    # multiple processes.
    with Pool(min(len(all_nodes), 64)) as p:
        results = p.map(get_all, all_nodes)

    with open("inventory.csv", "a") as out:
        for line in results:
            out.write(line + "\n")
