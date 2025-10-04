EOS to SONiC Minigraph Converter
================================

Overview
--------
This lightweight CLI parses an Arista EOS running-config and emits two SONiC minigraph-style XMLs: one for a SUP and one for a Linecard role. The generated XML captures hostname, ports, Port-Channels, VLANs, and BGP neighbors.

Status: pragmatic subset of minigraph
------------------------------------
This tool outputs a simplified XML structure that is easy to transform to official minigraph schema if desired. If you need strict compliance with a particular minigraph version, wire the `build_minigraph_xml` function to shape nodes accordingly.

Install & Run
-------------
No external dependencies required (Python 3.8+).

Example:

```bash
python3 /workspace/tools/eos2minigraph/eos2minigraph.py \
  --input /workspace/samples/eos_running_config.txt \
  --outdir /workspace/samples/out \
  --sup-hostname SUP-1 \
  --linecard-hostname LC-1
```

Outputs:
- `/workspace/samples/out/minigraph_sup.xml`
- `/workspace/samples/out/minigraph_linecard.xml`

Supported EOS constructs
------------------------
- `hostname <NAME>`
- `interface <IFNAME>` with:
  - `description <TEXT>`
  - `switchport access vlan <VID>`
  - `switchport trunk allowed vlan <VIDS>` (supports lists and ranges like `10-12,20`)
  - `channel-group <ID> mode <MODE>` (maps member to `Port-Channel<ID>`)
- `interface Port-Channel<ID>` with `description`
- `vlan <VID>` with `name <NAME>`
- `router bgp <ASN>` with neighbors:
  - `neighbor <ADDR> remote-as <ASN>`
  - `neighbor <ADDR> description <TEXT>`

Notes & Assumptions
-------------------
- Access/trunk VLAN membership is inferred for interfaces and aggregated to port-channels.
- BGP neighbor descriptions are associated by neighbor address.
- IP addressing, L3 subinterfaces, and advanced constructs are not yet parsed.
- Both SUP and Linecard XMLs are generated from the same parsed model; you can override hostnames/roles via flags.

Extending
---------
Enhance `parse_eos_config` to support more EOS features (SVIs, IPs, loopbacks, static routes, MLAG, VRFs). Then extend `build_minigraph_xml` to express them.

