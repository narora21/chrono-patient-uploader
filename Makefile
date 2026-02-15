APP_NAME := chrono-uploader
SRC := src/uploader.py
DIST_DIR := dist
BUILD_DIR := build
BUNDLE_FILES := metatag.json README.md

UNAME := $(shell uname -s)

.PHONY: dist clean install

install:
	pip install -r requirements.txt

dist: install
	pyinstaller --onefile --name $(APP_NAME) $(SRC)
ifeq ($(UNAME),Darwin)
	mkdir -p $(DIST_DIR)/$(APP_NAME)-mac
	cp $(BUNDLE_FILES) $(DIST_DIR)/$(APP_NAME)-mac/
	mv $(DIST_DIR)/$(APP_NAME) $(DIST_DIR)/$(APP_NAME)-mac/
	tar -czf $(DIST_DIR)/$(APP_NAME)-mac.tar.gz -C $(DIST_DIR) $(APP_NAME)-mac
	rm -rf $(DIST_DIR)/$(APP_NAME)-mac
	@echo "\nBuilt: $(DIST_DIR)/$(APP_NAME)-mac.tar.gz"
else
	mkdir -p $(DIST_DIR)/$(APP_NAME)-win
	cp $(BUNDLE_FILES) $(DIST_DIR)/$(APP_NAME)-win/
	mv $(DIST_DIR)/$(APP_NAME).exe $(DIST_DIR)/$(APP_NAME)-win/
	cd $(DIST_DIR) && python -m zipfile -c $(APP_NAME)-win.zip $(APP_NAME)-win
	rm -rf $(DIST_DIR)/$(APP_NAME)-win
	@echo "\nBuilt: $(DIST_DIR)/$(APP_NAME)-win.zip"
endif

clean:
	rm -rf $(DIST_DIR) $(BUILD_DIR) *.spec __pycache__ src/__pycache__
