# Hardware Tuning for Laptops (2023)

Running 7B-parameter models on a laptop requires careful VRAM and swap management.

## VRAM Budget

| Model | Format | Peak RAM |
|-------|--------|----------|
| LLaMA-7B | GGML q4_0 | ~4 GB |
| Vicuna-7B | GGML q4_0 | ~4 GB |

Keep 2 GB free for the OS and browser tabs. Close Chrome if you can — it eats RAM.

## Swap Setup

Swap is your safety net when VRAM is full:

```bash
# Check current swap
free -h

# Create a 4 GiB swap file (adjust to your disk space)
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

Add to `/etc/fstab` for persistence:

```
/swapfile none swap sw 0 0
```

## Fan Control

Laptops throttle hard. Keep fans spinning:

```bash
# Install fancontrol (lm-sensors must be configured first)
sudo apt install fancontrol lm-sensors
sudo sensors-detect   # accept all defaults

# Generate config from current readings
sudo pwmconfig
```

While benchmarking, set a minimum fan speed to avoid thermal throttling:

```bash
# Set fan1 to full speed (255 = max on most laptops)
echo 255 | sudo tee /sys/class/hwmon/hwmon1/pwm1
```

## Temperature Monitoring

Watch temps while generating. Throttling above 85 °C kills performance:

```bash
# Read GPU and CPU temps in real time
watch -n 1 'sensors | grep -E "(temp[0-9]|Package id 0)"'
```

If the laptop hits 90 °C, reduce `--ctx-size` (default 512 is fine for short prompts).
A smaller context window uses less RAM and generates faster.

## Quick Checklist

- [ ] Swap file ≥ 4 GiB
- [ ] Close unnecessary apps (browser, IDE)
- [ ] Fan control running or manual fan override set
- [ ] Model quantised to GGML q4_0 minimum
- [ ] Test with `./llama.cpp/llama-cli` before switching to llama-server
- [ ] Monitor temps — throttle if above 85 °C
