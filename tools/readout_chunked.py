#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Read data from SIS3316. Write raw (binary) data to files

Requires specification of approx. max file size per channel 
for split output files (readout is prioritized over filesize limits)
"""

import argparse
import io
import os
import sys
from pathlib import Path
from time import sleep

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
import sis3316

UNITS = {
    "TB": 1024**4,
    "GB": 1024**3,
    "MB": 1024**2,
    "KB": 1024,
    "B": 1,
}
OUTPATH = "data/"  # Default output directory
OUTEXT = ".dat"  # Default filename extension
PORT = 3333  # Default UDP Port number


def readout_loop_with_file_chunking(
    dev,
    channels,
    max_file_size,
    outpath,
    opts={},
    verbose=False,
):
    """Perform endless readout loop.

    Args:
        dev (sis3316.Sis3316): Readout device.
        channels (List[int]): List of channels to read from.
        outpath (str): Output directory
        max_file_size (int): Max file size in bytes per channel
        opts (Dict): Options to pass to sis3316 readout pipe.
        verbose (bool): If True, prints additional info to stderr
    """
    channel_info = {
        chan: {
            "outpath_prefix": f"{outpath}/ch{chan:02d}_",
            "chunk": 0,
            "total_bytes_received": 0,
            "bytes_in_current_file": 0,
        }
        for chan in channels
    }

    # Open files
    for chan in channels:
        channel_info[chan]["file_io"] = open_output_file(chan, channel_info)

    while True:
        try:
            # Channel readout
            dev.mem_toggle()
            for ch in channels:
                bytes_received = 0
                for ret in dev.readout_pipe(
                    chan_no=ch,
                    target=channel_info[ch]["file_io"],
                    target_skip=0,
                    opts=opts,
                ):  # per chunk
                    bytes_received = ret["transfered"] * 4  # words -> bytes

                channel_info[ch]["bytes_in_current_file"] += bytes_received
                channel_info[ch]["total_bytes_received"] += bytes_received

            if verbose:
                print_statistics(channel_info)

            # After channel readout, check if new file is needed
            for ch in channels:
                if channel_info[ch]["bytes_in_current_file"] >= max_file_size:
                    channel_info[ch]["file_io"].close()
                    channel_info[ch]["bytes_in_current_file"] = 0
                    channel_info[ch]["chunk"] += 1
                    channel_info[ch]["file_io"] = open_output_file(chan, channel_info)

            sleep(1)

        except KeyboardInterrupt:
            sys.stderr.write("\n\n\n##### KeyboardInterrupt #####\n\n\n")
            exit(0)


def open_output_file(channel, channel_info):
    """
    Args:
        channel (int)
        channel_info (Dict)

    Returns:
        io.FileIO
    """
    return io.FileIO(
        f"{channel_info[channel]['outpath_prefix']}"
        f"_chunk{channel_info[channel]['chunk']}{OUTEXT}",
        mode="w",
    )


def print_statistics(channel_info):
    """Prints statistics to stderr

    Args:
        channel_info (Dict)
    """
    output = []
    for ch, info in channel_info.items():
        size_chunk = sizeof_fmt(info["bytes_in_current_file"])
        size_total = sizeof_fmt(info["total_bytes_received"])
        output.append(
            f"ch{ch}\tsize (chunk {info['chunk']}): {size_chunk}"
            f"\tsize (total): {size_total}\n"
        )
    sys.stderr.write("".join(output))


def sizeof_fmt(num, suffix="B"):
    for unit in ["", "Ki", "Mi", "Gi", "Ti"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f} {unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f} Ti{suffix}"


def main():
    # Defaults
    chunksize = 1024 * 1024  # how many bytes to request at once
    opts = {"chunk_size": chunksize / 4}

    # Set the command line arguments
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument("host", type=str, help="hostname or ip address.")
    parser.add_argument(
        "port",
        type=int,
        nargs="?",
        default=PORT,
        help="UDP port number, default is %d" % PORT,
    )
    parser.add_argument(
        "--max-file-size",
        type=int,
        required=True,
        help="Approximate file size max",
    )
    parser.add_argument(
        "--unit",
        type=str,
        choices=UNITS.keys(),
        required=True,
        help=f"File size max unit",
    )
    parser.add_argument(
        "-c",
        "--channels",
        metavar="N",
        nargs="+",
        type=int,
        default=range(0, 16),
        help="channels to read, from 0 to 15 (all by default). \n"
        'Use shell expressions to specify a range (like "{0..7} {12..15}").',
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        metavar="PATH",
        default=OUTPATH,
        help="Output file directory" '\ndefault: "%s"' % OUTPATH,
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="print info to std err"
    )

    # Parse arguments
    args = parser.parse_args()

    # --channels
    for x in args.channels:
        if not 0 <= x <= 15:
            sys.stderr.write(f"{x} is not a valid channel number!\n")
            exit(1)

    channels = sorted(set(args.channels))  # deduplicate

    # --output
    if not args.max_file_size > 0:
        sys.stderr.write(
            f"{args.max_file_size} {args.unit} is not a valid file size!\n"
        )
        exit(1)
    max_file_size = args.max_file_size * UNITS[args.unit]

    outpath = args.output
    Path(outpath).mkdir(parents=True, exist_ok=True)

    # check no overwrite
    if list(Path(outpath).glob(f"*{OUTEXT}")):
        sys.stderr.write(
            f"{args.output}/*.{OUTEXT} files already exist!"
            " Not going to overwrite it.\n"
        )
        exit(1)

    # Prepare device
    host, port = args.host, args.port
    dev = sis3316.Sis3316_udp(host, port)
    dev.open()
    if not dev.configure():  # set channel numbers and so on.
        sys.stderr.write("Warning: After configure(), dev.status = false\n")
    dev.disarm()
    dev.arm()
    dev.ts_clear()
    dev.mem_toggle()  # flush the device memory to not to read a large chunk of old data

    if args.verbose:
        jumbo = "jumbo_ena" in getattr(dev, "flags")
        sys.stderr.write(
            f"ADC id: {dev.id}, serial: {hex(dev.serno)},"
            f" temp: {dev.temp} °C, jumbo_frame: {jumbo}\n"
        )
        sys.stderr.write(str(dev._readout_status()) + "\n")
        sys.stderr.write("---\n")

    readout_loop_with_file_chunking(
        dev=dev,
        channel=channels,
        max_file_size=max_file_size,
        outpath=outpath,
        opts=opts,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
