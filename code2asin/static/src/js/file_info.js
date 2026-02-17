/** @odoo-module **/

import { Component, onMounted, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";

export class FileInfoWidget extends Component {
    setup() {
        this.state = useState({
            fileSize: '-',
        });

        onMounted(() => {
            this.updateFileInfo();
        });
    }

    updateFileInfo() {
        const fileInput = document.querySelector('input[name="csv_file"]');
        const fileSizeDisplay = document.getElementById('file_size_display');
        
        if (fileInput && fileSizeDisplay) {
            fileInput.addEventListener('change', (event) => {
                if (event.target.files && event.target.files[0]) {
                    const file = event.target.files[0];
                    const sizeInBytes = file.size;
                    const sizeInKB = (sizeInBytes / 1024).toFixed(2);
                    const sizeInMB = (sizeInBytes / (1024 * 1024)).toFixed(2);
                    
                    let displaySize;
                    if (sizeInBytes < 1024) {
                        displaySize = `${sizeInBytes} bytes`;
                    } else if (sizeInBytes < 1024 * 1024) {
                        displaySize = `${sizeInKB} KB`;
                    } else {
                        displaySize = `${sizeInMB} MB`;
                    }
                    
                    fileSizeDisplay.textContent = displaySize;
                } else {
                    fileSizeDisplay.textContent = '-';
                }
            });
        }
    }
}

FileInfoWidget.template = "code2asin.FileInfoWidget";

registry.category("public_components").add("FileInfoWidget", FileInfoWidget);
