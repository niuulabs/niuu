---
type: topic
confidence: high
related_entities: []
source_ids: [src_e1b2c3d4e5f60401]
---

# Home network setup

## Compiled Truth

### Key Facts
- Fibre uplink with a router in the hallway closet; two mesh access points cover the house and the garden office.
- The garden office access point is on a wired backhaul — wireless backhaul kept dropping video calls.
- Guest network is isolated on its own VLAN; smart-home devices live there, not on the main network.
- The NAS takes nightly backups of the family laptops and syncs encrypted snapshots offsite.

### Relationships
- (none)

### Assessment
Stable since the wired backhaul fix. The remaining annoyance is the smart
doorbell, which needs the guest VLAN's captive portal disabled to re-pair.

## Timeline

- 2025-10-12: Mesh network installed. [Source: personal notes, journal, 2025-10-12]
- 2026-01-19: Garden office moved to wired backhaul after call drops. [Source: personal notes, journal, 2026-01-19]
