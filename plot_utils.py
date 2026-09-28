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
    matplotlib.use('Agg') # Headless plotting to prevent UI freezing
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
        # Cast to float upfront to handle datetime64 and reject string/object arrays silently
        v1_arr = np.asanyarray(v1_data).astype(float)
        v2_arr = np.asanyarray(v2_data).astype(float)
    except (ValueError, TypeError):
        return None

    try:
        if v1_arr.shape != v2_arr.shape:
            return None

        # Squeeze out singleton dimensions (e.g., (1, 1280, 1280) -> (1280, 1280))
        # This is common for SUVI or variables that include a single time dimension.
        v1_arr = np.squeeze(v1_arr)
        v2_arr = np.squeeze(v2_arr)

        slice_msg = ""

        # Handle N-D variables (e.g., multiple bands, levels, or time steps)
        # by recursively extracting the first index until we reach 2D.
        if v1_arr.ndim > 2:
            while v1_arr.ndim > 2:
                v1_arr = v1_arr[0]
                v2_arr = v2_arr[0]
            slice_msg = " [N-D Variable: Showing 2D Slice]"

        # --- 1D Array Plotting (Line Plots) ---
        if v1_arr.ndim == 1:
            # Safely handle massive 1D arrays to prevent Matplotlib from freezing
            if v1_arr.size > 20000:
                skip = max(1, v1_arr.size // 10000)
                v1_arr = v1_arr[::skip]
                v2_arr = v2_arr[::skip]
                slice_msg += " [Subsampled]"

            diff = v1_arr - v2_arr

            fig, axes = plt.subplots(1, 2, figsize=(12, 3.5))

            axes[0].plot(v1_arr, label=name1, color='tab:blue', alpha=0.7)
            axes[0].plot(v2_arr, label=name2, color='tab:orange', linestyle='--', alpha=0.7)
            axes[0].set_title("Value Overlay")
            axes[0].legend(loc='best', fontsize='small')
            axes[0].grid(True, linestyle=':', alpha=0.6)

            axes[1].plot(diff, color='tab:red', alpha=0.8)
            axes[1].set_title("Delta (Difference Locations)")
            axes[1].grid(True, linestyle=':', alpha=0.6)

            fig.suptitle(f"{var_name} Mismatch (1D View){slice_msg}", fontsize=12, y=1.05)

        # --- 2D Array Plotting (Image Plots) ---
        elif v1_arr.ndim == 2:
            # Subsample massive 2D grids (like ABI Radiances)
            if v1_arr.shape[0] > 1500 or v1_arr.shape[1] > 1500:
                skip_y = max(1, v1_arr.shape[0] // 1000)
                skip_x = max(1, v1_arr.shape[1] // 1000)
                v1_arr = v1_arr[::skip_y, ::skip_x]
                v2_arr = v2_arr[::skip_y, ::skip_x]

            diff = v1_arr - v2_arr

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

            fig.suptitle(f"{var_name} Mismatch (2D View){slice_msg}", fontsize=12, y=1.05)

        else:
            # 0-D arrays (scalars) are evaluated numerically but skipped for visual plotting
            return None

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

        # Calculate dynamic bounds
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
