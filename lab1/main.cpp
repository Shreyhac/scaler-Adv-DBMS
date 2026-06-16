// Name: Shreyansh Arora
// Roll No: 24BCS10252
// Lab 1: File I/O — Low-Level Syscalls

#include <iostream>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>

int main() {
    std::cout << "LAB 1: File I/O via low-level syscalls\n\n";

    const char* filepath = "lab1_output.txt";

    int fd = open(filepath, O_CREAT | O_WRONLY | O_TRUNC, S_IRUSR | S_IWUSR | S_IRGRP);
    if (fd < 0) {
        std::cerr << "open() failed\n";
        return 1;
    }
    std::cout << "open()  -> fd=" << fd << "\n";

    const char* data =
        "Lab 1: Low-level file I/O in C++\n"
        "Path: write() -> VFS -> Page Cache -> fsync -> Block Driver -> Disk\n";

    ssize_t written = write(fd, data, strlen(data));
    if (written < 0) {
        std::cerr << "write() failed\n";
        close(fd);
        return 1;
    }
    std::cout << "write() -> " << written << " bytes\n";

    if (fsync(fd) < 0) {
        std::cerr << "fsync() failed\n";
        close(fd);
        return 1;
    }
    std::cout << "fsync() -> flushed to disk\n";

    close(fd);
    std::cout << "close() -> done\n";

    std::cout << "\n--- read back ---\n";
    fd = open(filepath, O_RDONLY);
    if (fd < 0) { std::cerr << "open() for read failed\n"; return 1; }

    char buf[512] = {};
    ssize_t bytes_read = read(fd, buf, sizeof(buf) - 1);
    if (bytes_read < 0) { close(fd); return 1; }

    std::cout << "read()  -> " << bytes_read << " bytes\n";
    std::cout << buf;
    close(fd);

    return 0;
}
