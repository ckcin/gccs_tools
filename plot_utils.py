#!/usr/bin/env python3
"""
Plotting utilities for GOES data comparison.
Handles 2D difference arrays and spatial maps using Matplotlib and Cartopy.
"""
import os
import tempfile

try:
    import numpy as np
    import matplotlib
    matplotlib.use('Agg') # Headless plotting
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    HAS_CARTOPY = True
except ImportError:
    HAS_CARTOPY = False

def generate_diff_plot(var_name, v1_data, v2_data, name1, name2):
    """Generates a graphical plot of the numeric differences between two arrays."""
    if not HAS_MPL: return None
    try:
        v1_arr = np.asanyarray(v1_data)
        v2_arr = np.asanyarray(v2_data)

        if v1_arr.shape != v2_arr.shape:
            return None

        # Only plot 2D variables (like ABI images)
        if v1_arr.ndim != 2:
            return None

        if v1_arr.shape[0] > 1500 or v1_arr.shape[1] > 1500:
            skip_y = max(1, v1_arr.shape[0] // 1000)
            skip_x = max(1, v1_arr.shape[1] // 1000)
            v1_arr = v1_arr[::skip_y, ::skip_x]
            v2_arr = v2_arr[::skip_y, ::skip_x]

        diff = v1_arr.astype(float) - v2_arr.astype(float)

        fig, axes = plt.subplots(1, 3, figsize=(12, 3.5))

        im0 = axes[0].imshow(v1_arr, cmap='viridis', aspect='auto')
        axes[0].set_title(f"{name1}")
        fig.colorbar(im0, ax=axes[0], fraction=0.046, pad=0.04)

        vmax = np.nanmax(np.abs(diff))
        if vmax == 0 or np.isnan(vmax): vmax = 1
        im1 = axes[1].imshow(diff, cmap='coolwarm', vmin=-vmax, vmax=vmax, aspect='auto')
        axes[1].set_title("Delta (Difference Locations)")
        fig.colorbar(im1, ax=axes[1], fraction=0.046, pad=0.04)

        im2 = axes[2].imshow(v2_arr, cmap='viridis', aspect='auto')
        axes[2].set_title(f"{name2}")
        fig.colorbar(im2, ax=axes[2], fraction=0.046, pad=0.04)

        fig.suptitle(f"{var_name} Mismatch (2D View)", fontsize=12, y=1.05)
        plt.tight_layout()

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(tmp_fd)
        plt.savefig(tmp_path, dpi=150, bbox_inches='tight')
        plt.close()
        return tmp_path

    except Exception as e:
        print(f"Warning: Failed to generate diff plot: {e}")
        return None

def generate_spatial_plot(entity_name, lat1, lon1, lat2, lon2, name1, name2):
    """Generates a 1x3 spatial subplot mapping Source 1, Differences, and Source 2."""
    if not HAS_MPL: return None
    try:
        mask1 = ~(np.isnan(lat1) | np.isnan(lon1))
        lat1, lon1 = lat1[mask1], lon1[mask1]

        mask2 = ~(np.isnan(lat2) | np.isnan(lon2))
        lat2, lon2 = lat2[mask2], lon2[mask2]

        if len(lat1) == 0 and len(lat2) == 0:
            return None

        fig = plt.figure(figsize=(12, 4))

        all_lats = np.concatenate([lat1, lat2])
        all_lons = np.concatenate([lon1, lon2])
        if len(all_lats) > 0:
            min_lat, max_lat = np.min(all_lats) - 2, np.max(all_lats) + 2
            min_lon, max_lon = np.min(all_lons) - 2, np.max(all_lons) + 2
        else:
            min_lat, max_lat, min_lon, max_lon = -90, 90, -180, 180

        extent = [min_lon, max_lon, min_lat, max_lat]
        axes = []

        if HAS_CARTOPY:
            for i in range(1, 4):
                ax = fig.add_subplot(1, 3, i, projection=ccrs.PlateCarree())
                ax.add_feature(cfeature.COASTLINE, linewidth=0.8)
                ax.add_feature(cfeature.BORDERS, linewidth=0.5, linestyle=':')
                ax.add_feature(cfeature.STATES, linewidth=0.3, linestyle=':')
                gl = ax.gridlines(draw_labels=True, linestyle='--', alpha=0.5)
                gl.top_labels = False
                gl.right_labels = False
                if i > 1: gl.left_labels = False
                ax.set_extent(extent, crs=ccrs.PlateCarree())
                axes.append(ax)
            scatter_kwargs = {'transform': ccrs.PlateCarree()}
        else:
            for i in range(1, 4):
                ax = fig.add_subplot(1, 3, i)
                ax.grid(True, linestyle='--', alpha=0.5)
                ax.set_xlim([min_lon, max_lon])
                ax.set_ylim([min_lat, max_lat])
                if i == 1: ax.set_ylabel("Latitude")
                ax.set_xlabel("Longitude")
                axes.append(ax)
            scatter_kwargs = {}

        axes[0].set_title(f"{name1}")
        if len(lat1) > 0:
            axes[0].scatter(lon1, lat1, color='tab:blue', s=4, alpha=0.6, marker='o', **scatter_kwargs)

        axes[1].set_title("Overlay / Differences")
        if len(lat1) > 0:
            axes[1].scatter(lon1, lat1, color='tab:blue', s=4, alpha=0.6, marker='o', label=name1, **scatter_kwargs)
        if len(lat2) > 0:
            axes[1].scatter(lon2, lat2, color='tab:red', s=4, alpha=0.6, marker='x', label=name2, **scatter_kwargs)
        axes[1].legend(loc="upper right", fontsize='small')

        axes[2].set_title(f"{name2}")
        if len(lat2) > 0:
            axes[2].scatter(lon2, lat2, color='tab:red', s=4, alpha=0.6, marker='x', **scatter_kwargs)

        fig.suptitle(f"{entity_name.capitalize()}s Spatial Distribution", fontsize=14, y=1.05)
        plt.tight_layout()

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(tmp_fd)
        plt.savefig(tmp_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        return tmp_path
    except Exception as e:
        print(f"Warning: Failed to generate spatial plot: {e}")
        return None
