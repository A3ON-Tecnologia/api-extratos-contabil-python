
import win32wnet
import sys

try:
    drive = "J:"
    unc_path = win32wnet.WNetGetConnection(drive)
    print(f"UNC Path for {drive} is: {unc_path}")
except Exception as e:
    print(f"Error getting UNC path: {e}")
