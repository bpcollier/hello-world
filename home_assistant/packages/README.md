# Home Assistant Packages

## Setup

To use package files, add the following to your `configuration.yaml`:

```yaml
homeassistant:
  packages: !include_dir_named packages/
```

Then restart Home Assistant. Each `.yaml` file in this folder is loaded as a package.

## Packages

### `babysitter_presence.yaml`

Detects when the babysitter's device is on the UniFi network and enables/disables
automations accordingly.

See the comments at the top of that file for setup instructions.
