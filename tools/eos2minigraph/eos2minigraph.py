#!/usr/bin/env python3
"""
EOS to SONiC Minigraph converter (SUP and Linecard)

This tool parses an Arista EOS running-config and emits two XML files representing
SUP and Linecard minigraphs. The XML structure produced is a pragmatic subset that
captures hostname, interfaces, Port-Channels, VLANs, and BGP neighbors.

Notes:
- The produced XML structure is intentionally simple and self-describing. If your
  environment requires strict adherence to a specific minigraph schema, adjust the
  writer functions accordingly. The internal model aims to make such adjustments easy.

Usage:
  python3 eos2minigraph.py --input /path/to/eos_running_config.txt --outdir /tmp/out

"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import xml.etree.ElementTree as ET


# ------------------------------
# Data model
# ------------------------------


@dataclass
class Vlan:
    vlan_id: int
    name: Optional[str] = None
    members: Set[str] = field(default_factory=set)


@dataclass
class PortChannel:
    name: str
    description: Optional[str] = None
    trunk_vlans: Set[int] = field(default_factory=set)
    access_vlan: Optional[int] = None
    members: Set[str] = field(default_factory=set)


@dataclass
class Interface:
    name: str
    description: Optional[str] = None
    access_vlan: Optional[int] = None
    trunk_vlans: Set[int] = field(default_factory=set)
    portchannel: Optional[str] = None  # e.g., "Port-Channel1"


@dataclass
class BgpNeighbor:
    neighbor_address: str
    remote_asn: int
    description: Optional[str] = None


@dataclass
class EosConfigModel:
    hostname: Optional[str] = None
    interfaces: Dict[str, Interface] = field(default_factory=dict)
    portchannels: Dict[str, PortChannel] = field(default_factory=dict)
    vlans: Dict[int, Vlan] = field(default_factory=dict)
    local_asn: Optional[int] = None
    bgp_neighbors: List[BgpNeighbor] = field(default_factory=list)


# ------------------------------
# EOS config parser
# ------------------------------


INTERFACE_START_RE = re.compile(r"^interface\s+(?P<name>\S+)\s*$")
SECTION_END_RE = re.compile(r"^!\s*$")
HOSTNAME_RE = re.compile(r"^hostname\s+(?P<hostname>\S+)\s*$")
DESC_RE = re.compile(r"^\s*description\s+(?P<desc>.+?)\s*$")
SW_ACCESS_RE = re.compile(r"^\s*switchport\s+access\s+vlan\s+(?P<vlan>\d+)\s*$")
SW_TRUNK_RE = re.compile(r"^\s*switchport\s+trunk\s+allowed\s+vlan\s+(?P<list>.+?)\s*$")
CHANNEL_GROUP_RE = re.compile(r"^\s*channel-group\s+(?P<num>\d+)\s+mode\s+(?P<mode>\S+)\s*$")
VLAN_START_RE = re.compile(r"^vlan\s+(?P<vlan>\d+)\s*$")
VLAN_NAME_RE = re.compile(r"^\s*name\s+(?P<name>\S+)\s*$")
BGP_START_RE = re.compile(r"^router\s+bgp\s+(?P<asn>\d+)\s*$")
BGP_NEI_RE = re.compile(r"^\s*neighbor\s+(?P<addr>\S+)\s+remote-as\s+(?P<asn>\d+)\s*$")
BGP_NEI_DESC_RE = re.compile(r"^\s*neighbor\s+(?P<addr>\S+)\s+description\s+(?P<desc>.+?)\s*$")


def _parse_vlan_list(list_expr: str) -> Set[int]:
    result: Set[int] = set()
    parts = [p.strip() for p in list_expr.split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            try:
                start = int(start_s)
                end = int(end_s)
            except ValueError:
                continue
            if end < start:
                start, end = end, start
            for v in range(start, end + 1):
                result.add(v)
        else:
            try:
                result.add(int(part))
            except ValueError:
                continue
    return result


def parse_eos_config(text: str) -> EosConfigModel:
    model = EosConfigModel()
    lines = text.splitlines()

    i = 0
    in_bgp = False
    while i < len(lines):
        line = lines[i].rstrip()

        # Hostname (global)
        m = HOSTNAME_RE.match(line)
        if m:
            model.hostname = m.group("hostname")
            i += 1
            continue

        # Interface sections
        m = INTERFACE_START_RE.match(line)
        if m:
            iface_name = m.group("name")
            desc: Optional[str] = None
            access_vlan: Optional[int] = None
            trunk_vlans: Set[int] = set()
            portchannel: Optional[str] = None

            i += 1
            while i < len(lines) and not SECTION_END_RE.match(lines[i]):
                iline = lines[i].rstrip()
                md = DESC_RE.match(iline)
                if md:
                    desc = md.group("desc")
                    i += 1
                    continue
                ma = SW_ACCESS_RE.match(iline)
                if ma:
                    access_vlan = int(ma.group("vlan"))
                    i += 1
                    continue
                mt = SW_TRUNK_RE.match(iline)
                if mt:
                    trunk_vlans |= _parse_vlan_list(mt.group("list"))
                    i += 1
                    continue
                mg = CHANNEL_GROUP_RE.match(iline)
                if mg:
                    portchannel = f"Port-Channel{mg.group('num')}"
                    i += 1
                    continue
                i += 1

            # Register interface
            model.interfaces[iface_name] = Interface(
                name=iface_name,
                description=desc,
                access_vlan=access_vlan,
                trunk_vlans=trunk_vlans,
                portchannel=portchannel,
            )

            # If in a port-channel, create/update the aggregate
            if portchannel:
                pc = model.portchannels.get(portchannel)
                if pc is None:
                    pc = PortChannel(name=portchannel)
                    model.portchannels[portchannel] = pc
                pc.members.add(iface_name)

            i += 1
            continue

        # VLAN sections
        m = VLAN_START_RE.match(line)
        if m:
            vlan_id = int(m.group("vlan"))
            name: Optional[str] = None
            i += 1
            while i < len(lines) and not SECTION_END_RE.match(lines[i]):
                iline = lines[i].rstrip()
                mn = VLAN_NAME_RE.match(iline)
                if mn:
                    name = mn.group("name")
                    i += 1
                    continue
                i += 1

            vlan = model.vlans.get(vlan_id)
            if vlan is None:
                vlan = Vlan(vlan_id=vlan_id)
                model.vlans[vlan_id] = vlan
            vlan.name = name
            i += 1
            continue

        # BGP section
        m = BGP_START_RE.match(line)
        if m:
            model.local_asn = int(m.group("asn"))
            in_bgp = True
            i += 1
            while i < len(lines) and not SECTION_END_RE.match(lines[i]):
                iline = lines[i].rstrip()
                mn = BGP_NEI_RE.match(iline)
                if mn:
                    addr = mn.group("addr")
                    asn = int(mn.group("asn"))
                    model.bgp_neighbors.append(BgpNeighbor(neighbor_address=addr, remote_asn=asn))
                    i += 1
                    continue
                md = BGP_NEI_DESC_RE.match(iline)
                if md:
                    # Backfill description to the most recent neighbor that matches addr
                    addr = md.group("addr")
                    desc = md.group("desc")
                    for nb in reversed(model.bgp_neighbors):
                        if nb.neighbor_address == addr and nb.description is None:
                            nb.description = desc
                            break
                    i += 1
                    continue
                i += 1
            in_bgp = False
            i += 1
            continue

        i += 1

    # Post-processing: infer VLAN membership from interfaces and port-channels
    for ifname, iface in model.interfaces.items():
        if iface.access_vlan is not None:
            vlan = model.vlans.setdefault(iface.access_vlan, Vlan(vlan_id=iface.access_vlan))
            vlan.members.add(ifname)
        if iface.trunk_vlans:
            for v in iface.trunk_vlans:
                vlan = model.vlans.setdefault(v, Vlan(vlan_id=v))
                vlan.members.add(ifname)
        if iface.portchannel:
            # Port-channel membership already tracked in aggregate
            pass

    # Propagate VLANs from member interfaces to Port-Channels (if channelized)
    for pc_name, pc in model.portchannels.items():
        # Aggregate access VLAN if all members share the same access VLAN
        member_ifaces = [model.interfaces[m] for m in pc.members if m in model.interfaces]
        access_vlans = {iface.access_vlan for iface in member_ifaces if iface.access_vlan is not None}
        if len(access_vlans) == 1:
            pc.access_vlan = next(iter(access_vlans))
        # Union of trunk VLANs
        trunk_union: Set[int] = set()
        for iface in member_ifaces:
            trunk_union |= iface.trunk_vlans
        pc.trunk_vlans = trunk_union
        # Ensure VLAN membership includes the aggregate port-channel
        if pc.access_vlan is not None:
            vlan = model.vlans.setdefault(pc.access_vlan, Vlan(vlan_id=pc.access_vlan))
            vlan.members.add(pc_name)
        for v in pc.trunk_vlans:
            vlan = model.vlans.setdefault(v, Vlan(vlan_id=v))
            vlan.members.add(pc_name)

    return model


# ------------------------------
# Minigraph writers (XML)
# ------------------------------


def _indent(elem: ET.Element, level: int = 0) -> None:
    # Pretty-print the XML without changing logical structure
    indent_str = "  "
    i = "\n" + level * indent_str
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + indent_str
        for child in elem:
            _indent(child, level + 1)
        if not child.tail or not child.tail.strip():  # type: ignore[name-defined]
            child.tail = i
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = i


def build_minigraph_xml(model: EosConfigModel, role: str, hostname_override: Optional[str] = None) -> ET.ElementTree:
    root = ET.Element("minigraph")

    meta = ET.SubElement(root, "metadata")
    ET.SubElement(meta, "hostname").text = (hostname_override or model.hostname or "unknown").strip()
    ET.SubElement(meta, "role").text = role
    if model.local_asn is not None:
        ET.SubElement(meta, "local_asn").text = str(model.local_asn)

    # Interfaces
    ports_el = ET.SubElement(root, "ports")
    for ifname in sorted(model.interfaces.keys()):
        iface = model.interfaces[ifname]
        port_el = ET.SubElement(ports_el, "port")
        ET.SubElement(port_el, "name").text = iface.name
        if iface.description:
            ET.SubElement(port_el, "description").text = iface.description
        if iface.access_vlan is not None:
            ET.SubElement(port_el, "access_vlan").text = str(iface.access_vlan)
        if iface.trunk_vlans:
            ET.SubElement(port_el, "trunk_vlans").text = ",".join(str(v) for v in sorted(iface.trunk_vlans))
        if iface.portchannel:
            ET.SubElement(port_el, "portchannel").text = iface.portchannel

    # Port-Channels
    if model.portchannels:
        pcs_el = ET.SubElement(root, "portchannels")
        for pcname in sorted(model.portchannels.keys()):
            pc = model.portchannels[pcname]
            pc_el = ET.SubElement(pcs_el, "portchannel")
            ET.SubElement(pc_el, "name").text = pc.name
            if pc.description:
                ET.SubElement(pc_el, "description").text = pc.description
            if pc.access_vlan is not None:
                ET.SubElement(pc_el, "access_vlan").text = str(pc.access_vlan)
            if pc.trunk_vlans:
                ET.SubElement(pc_el, "trunk_vlans").text = ",".join(str(v) for v in sorted(pc.trunk_vlans))
            if pc.members:
                ET.SubElement(pc_el, "members").text = ",".join(sorted(pc.members))

    # VLANs
    if model.vlans:
        vlans_el = ET.SubElement(root, "vlans")
        for vid in sorted(model.vlans.keys()):
            vlan = model.vlans[vid]
            vlan_el = ET.SubElement(vlans_el, "vlan")
            ET.SubElement(vlan_el, "vlanid").text = str(vlan.vlan_id)
            if vlan.name:
                ET.SubElement(vlan_el, "name").text = vlan.name
            if vlan.members:
                ET.SubElement(vlan_el, "members").text = ",".join(sorted(vlan.members))

    # BGP
    if model.local_asn is not None or model.bgp_neighbors:
        bgp_el = ET.SubElement(root, "bgp")
        if model.local_asn is not None:
            ET.SubElement(bgp_el, "asn").text = str(model.local_asn)
        if model.bgp_neighbors:
            nbs_el = ET.SubElement(bgp_el, "neighbors")
            for nb in model.bgp_neighbors:
                nb_el = ET.SubElement(nbs_el, "neighbor")
                ET.SubElement(nb_el, "address").text = nb.neighbor_address
                ET.SubElement(nb_el, "remote_asn").text = str(nb.remote_asn)
                if nb.description:
                    ET.SubElement(nb_el, "description").text = nb.description

    tree = ET.ElementTree(root)
    _indent(root)
    return tree


# ------------------------------
# CLI
# ------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Convert EOS running-config to SONiC minigraph XML (SUP and Linecard)")
    parser.add_argument("--input", required=True, help="Path to EOS running-config text file")
    parser.add_argument("--outdir", required=True, help="Output directory for generated XML files")
    parser.add_argument("--sup-hostname", help="Override hostname for SUP minigraph")
    parser.add_argument("--linecard-hostname", help="Override hostname for Linecard minigraph")
    parser.add_argument("--sup-role", default="SUP", help="Role value for SUP (default: SUP)")
    parser.add_argument("--linecard-role", default="Linecard", help="Role value for Linecard (default: Linecard)")

    args = parser.parse_args(argv)

    input_path = Path(args.input)
    outdir = Path(args.outdir)
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 2
    outdir.mkdir(parents=True, exist_ok=True)

    text = input_path.read_text(encoding="utf-8", errors="ignore")
    model = parse_eos_config(text)

    # Generate SUP
    sup_tree = build_minigraph_xml(model, role=args.sup_role, hostname_override=args.sup_hostname)
    sup_path = outdir / "minigraph_sup.xml"
    sup_tree.write(sup_path, encoding="utf-8", xml_declaration=True)

    # Generate Linecard
    lc_tree = build_minigraph_xml(model, role=args.linecard_role, hostname_override=args.linecard_hostname)
    lc_path = outdir / "minigraph_linecard.xml"
    lc_tree.write(lc_path, encoding="utf-8", xml_declaration=True)

    print(f"Wrote: {sup_path}")
    print(f"Wrote: {lc_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

