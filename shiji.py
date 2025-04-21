import sys
import json
import time
import requests
import logging
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QScrollArea, QGridLayout, QLabel, QSpinBox, QPushButton, 
                            QLineEdit, QHBoxLayout, QFrame, QSizePolicy, QMessageBox, 
                            QInputDialog, QTableWidget, QTableWidgetItem, QDialog)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QThreadPool, QRunnable, QUrl
from PyQt5.QtGui import QPixmap, QFont, QColor
from PyQt5.QtGui import QDesktopServices
import webbrowser
from functools import partial

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ImageLoader(QRunnable):
    def __init__(self, url, callback):
        super().__init__()
        self.url = url
        self.callback = callback

    def run(self):
        try:
            response = requests.get(self.url, timeout=10)
            if response.status_code == 200:
                self.callback(response.content)
        except Exception as e:
            logger.error(f"图片下载失败: {str(e)}")

class ProductMonitor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.product_cache = {}  # 所有历史商品缓存
        self.min_price_products = {}  # 同名商品的最低价记录
        self.refresh_interval = 5
        self.cookies = self.load_cookies()
        self.thread_pool = QThreadPool.globalInstance()
        self.thread_pool.setMaxThreadCount(5)  # 限制最大线程数
        self.image_cache = {}  # 用于缓存已加载的图片
        self.last_refresh_count = 0  # 上次刷新获取的商品数量
        self.total_products_count = 0  # 总商品数量
        self.last_refresh_ids = set()  # 上次刷新的商品ID集合
        self.columns_count = 8  # 默认每行显示8列
        self.sidebar_columns_count = 3  # 右侧边栏默认3列
        self.api_cooldown = 2000  # API请求冷却时间(毫秒)
        self.card_width = 150  # 卡片宽度
        self.card_height = 180  # 卡片高度
        self.is_paused = False  # 是否暂停自动刷新
        self.remaining_time = 0  # 下次刷新剩余时间（秒）
        self.price_alert_enabled = False  # 价格提醒开关
        self.price_alert_threshold = 0  # 价格提醒阈值
        self.load_settings()  # 加载设置
        self.load_product_cache()  # 加载之前保存的商品缓存
        self.init_ui()
        self.worker_thread = WorkerThread(self)
        self.worker_thread.update_signal.connect(self.update_products)
        self.worker_thread.error_signal.connect(self.handle_error)
        self.worker_thread.auto_load_signal.connect(self.auto_load_more)
        
        # 从本地加载历史最低价记录
        self.load_min_price_products()
        
        # 窗口大小变化时重新布局
        self.resizeEvent = self.on_resize
        
        # 启动倒计时定时器（每秒更新一次）
        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(self.update_countdown)
        self.countdown_timer.start(1000)  # 每秒更新一次

    def on_resize(self, event):
        """窗口大小变化时调整布局"""
        try:
            # 计算每行可以放置的卡片数量
            scroll_width = self.scroll.width() - 30  # 减去滚动条宽度和边距
            visible_columns = max(1, scroll_width // (self.card_width + 8))  # 8是间距
            
            # 如果计算的列数与设置不同，且不为0，则更新列数
            if visible_columns != self.columns_count and visible_columns > 0:
                logger.info(f"自动调整列数: {self.columns_count} -> {visible_columns}")
                self.columns_count = visible_columns
                self.columns_spinner.setValue(visible_columns)
                
                # 重新布局
                self.refresh_layout_with_recent_first()
                
            # 同样计算右侧边栏的列数
            sidebar_width = self.sidebar_scroll.width() - 30
            visible_sidebar_columns = max(1, sidebar_width // (self.card_width + 8))
            
            if visible_sidebar_columns != self.sidebar_columns_count and visible_sidebar_columns > 0:
                logger.info(f"自动调整右侧列数: {self.sidebar_columns_count} -> {visible_sidebar_columns}")
                self.sidebar_columns_count = visible_sidebar_columns
                self.update_sidebar()
        except Exception as e:
            logger.error(f"调整布局失败: {str(e)}")
        
        super().resizeEvent(event)
    
    def load_settings(self):
        """加载设置"""
        try:
            if os.path.exists('settings.json'):
                with open('settings.json', 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                self.columns_count = settings.get('columns_count', 8)
                self.sidebar_columns_count = settings.get('sidebar_columns_count', 3)
                self.api_cooldown = settings.get('api_cooldown', 2000)
                self.card_width = settings.get('card_width', 150)
                self.card_height = settings.get('card_height', 180)
                self.price_alert_threshold = settings.get('price_alert_threshold', 0)
                self.price_alert_enabled = settings.get('price_alert_enabled', False)
                logger.info(f"已加载设置: 列数={self.columns_count}, 冷却时间={self.api_cooldown}ms")
        except Exception as e:
            logger.error(f"加载设置失败: {str(e)}")
    
    def save_settings(self):
        """保存设置"""
        try:
            settings = {
                'columns_count': self.columns_count,
                'sidebar_columns_count': self.sidebar_columns_count,
                'api_cooldown': self.api_cooldown,
                'card_width': self.card_width,
                'card_height': self.card_height,
                'price_alert_threshold': self.price_alert_threshold,
                'price_alert_enabled': self.price_alert_enabled
            }
            with open('settings.json', 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False)
            logger.info("已保存设置")
        except Exception as e:
            logger.error(f"保存设置失败: {str(e)}")
            
    def update_columns(self, value):
        """更新列数"""
        self.columns_count = value
        self.save_settings()
        self.refresh_layout_with_recent_first()
        logger.info(f"更新列数为: {value}")
        
    def update_cooldown(self, value):
        """更新API冷却时间"""
        self.api_cooldown = value * 1000  # 秒转毫秒
        self.save_settings()
        self.worker_thread.api_cooldown = self.api_cooldown
        logger.info(f"更新API冷却时间为: {value}秒 ({self.api_cooldown}ms)")

    def handle_error(self, error_message):
        """处理错误信息"""
        self.update_status(f"发生错误: {error_message}")  # 更新状态栏显示错误信息
        logger.error(f"错误: {error_message}")  # 在日志中记录错误信息
        QMessageBox.warning(self, "错误", f"发生错误: {error_message}")

    def load_image(self, label, image_data):
        """异步加载图片并缓存"""
        try:
            # 检查是否已经缓存
            if image_data in self.image_cache:
                pixmap = self.image_cache[image_data]
            else:
                pixmap = QPixmap()
                pixmap.loadFromData(image_data)
                pixmap = pixmap.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)  # 缩放图片
                self.image_cache[image_data] = pixmap  # 缓存图片

            label.setPixmap(pixmap)
        except Exception as e:
            logger.error(f"图片加载失败: {str(e)}")
            label.setText("图片加载失败")

    def update_status(self, message):
        """更新状态栏的消息"""
        self.status_bar.setText(message)

    def filter_products(self):
        """根据搜索框中的内容过滤商品"""
        search_text = self.search_input.text().lower()
        visible_count = 0
        
        for product_id, widget in self.product_cache.items():
            product_name = widget.findChild(QLabel, "name").text().lower()
            if search_text in product_name:
                widget.setVisible(True)
                visible_count += 1
            else:
                widget.setVisible(False)
                
        self.update_status(f"显示 {visible_count} 件商品 (共 {len(self.product_cache)} 件)")

    def filter_sidebar_products(self):
        """根据搜索框中的内容过滤右侧历史最低价商品"""
        search_text = self.sidebar_search_input.text().lower()
        visible_count = 0
        
        try:
            # 安全地清除布局中的所有控件
            while self.sidebar_grid.count():
                item = self.sidebar_grid.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()
                
            # 重新添加符合条件的商品
            filtered_products = [p for p in self.min_price_products.values() 
                                if search_text in p['name'].lower()]
            
            # 按价格排序（从低到高）
            filtered_products = sorted(filtered_products, key=lambda x: x['price'])
            
            # 以网格形式显示过滤后的商品
            row = 0
            col = 0
            for product in filtered_products:
                # 确保产品有id字段
                min_price_product = {
                    'id': product.get('name', '').replace(' ', '_'),  # 使用name作为id
                    'name': product.get('name', '未知商品'),
                    'price': product.get('price', 0),
                    'image': product.get('image', ''),
                    'detail_url': product.get('url', '')
                }
                # 使用同样的卡片样式，但不标记为新商品
                card = self.add_product_card(min_price_product, is_new=False)
                self.sidebar_grid.addWidget(card, row, col)
                col += 1
                if col >= self.sidebar_columns_count:  # 使用设置的列数
                    col = 0
                    row += 1
                visible_count += 1
                
            self.update_status(f"侧边栏显示 {visible_count} 件最低价商品 (共 {len(self.min_price_products)} 件)")
        except Exception as e:
            logger.error(f"筛选边栏商品时出错: {str(e)}")
            self.update_status(f"筛选边栏商品时出错: {str(e)}")

    def clear_layout(self, layout):
        """清除布局中的所有控件"""
        if not layout:
            return
            
        while layout.count():
            item = layout.takeAt(0)
            if item:
                if item.widget():
                    item.widget().setParent(None)
                    item.widget().deleteLater()
                elif item.layout():
                    self.clear_layout(item.layout())

    def create_sidebar_item(self, product):
        """创建侧边栏商品项 - 列表形式，更紧凑的版本"""
        item = QFrame()
        item.setObjectName(f"sidebar_item_{product['name']}")
        item.setStyleSheet("""
            QFrame {
                background: #F4F4F4;
                border-radius: 4px;
                padding: 6px;
                margin-bottom: 6px;
            }
            QFrame:hover {
                background: #E6F1FC;
                border: 1px solid #409EFF;
            }
        """)
        
        # 使用水平布局
        layout = QHBoxLayout(item)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        
        # 添加商品预览图
        img_label = QLabel()
        img_label.setFixedSize(40, 40)  # 缩小图片
        img_label.setAlignment(Qt.AlignCenter)
        img_label.setStyleSheet("background: #F5F7FA; border-radius: 4px;")
        
        self.thread_pool.start(ImageLoader(
            product['image'],
            partial(self.load_image, img_label)
        ))
        layout.addWidget(img_label)

        # 商品信息垂直布局
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        
        name_label = QLabel(product['name'])
        name_label.setStyleSheet("font: 12px 'Microsoft YaHei'; color: #303133;")
        name_label.setWordWrap(True)
        name_label.setMaximumHeight(40)
        name_label.setToolTip(product['name'])  # 添加工具提示，鼠标悬停时显示完整名称
        info_layout.addWidget(name_label)

        # 价格和时间的水平布局
        price_time_layout = QHBoxLayout()
        price_time_layout.setSpacing(4)
        
        price_label = QLabel(f"¥{product['price']:.2f}")
        price_label.setStyleSheet("font: bold 14px; color: #E6A23C;")
        price_time_layout.addWidget(price_label)
        
        price_time_layout.addStretch()
        
        # 时间标签
        time_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(product['timestamp']))
        time_label = QLabel(f"{time_str}")
        time_label.setStyleSheet("font: 10px; color: #909399;")
        price_time_layout.addWidget(time_label)
        
        info_layout.addLayout(price_time_layout)
        layout.addLayout(info_layout, 1)  # 1表示拉伸因子，使信息部分占据更多空间
        
        # 添加查看按钮
        view_btn = QPushButton("查看")
        view_btn.setFixedSize(36, 24)  # 缩小按钮
        view_btn.setStyleSheet("""
            background: #409EFF;
            color: white;
            border-radius: 4px;
            font: 11px;
            padding: 1px;
        """)
        view_btn.clicked.connect(lambda: self.open_url(product['url']))
        layout.addWidget(view_btn)

        # 点击整个卡片也可以打开链接
        item.mousePressEvent = lambda e: self.open_url(product['url'])
        return item

    def refresh_data(self):
        """手动刷新数据"""
        # 如果暂停了，不自动刷新，但手动点击刷新按钮仍然可以刷新
        # 如果是自动调用（由定时器触发）且已暂停，则忽略
        if self.is_paused and self.sender() == self.timer:
            return
            
        self.refresh_btn.setEnabled(False)
        self.load_more_btn.setEnabled(False)
        # 标记为刷新操作，会重置nextId
        self.worker_thread.refresh_data()
        self.worker_thread.start()  # 启动获取商品数据的线程
        self.update_status("正在刷新数据，将自动加载多页...")
        # 更新倒计时
        self.remaining_time = self.refresh_interval
        
    def update_refresh_interval(self, value):
        """更新刷新间隔"""
        self.refresh_interval = value
        if self.timer.isActive():
            self.timer.stop()  # 停止当前定时器
        self.timer.start(self.refresh_interval * 1000)  # 启动新的定时器
        self.remaining_time = self.refresh_interval  # 重置倒计时
        self.update_status(f"刷新间隔已更改为 {self.refresh_interval} 秒")

    def save_cookie(self):
        """保存Cookie到文件"""
        cookie_value = self.cookie_input.text()
        cookies = {"cookie": cookie_value}
        try:
            with open('cookies.json', 'w') as f:
                json.dump(cookies, f)
            self.update_status("Cookie已保存")
            logger.info("Cookie已保存")
        except Exception as e:
            logger.error(f"保存Cookie失败: {str(e)}")
            QMessageBox.warning(self, "保存失败", f"保存Cookie失败: {str(e)}")

    def load_cookies(self):
        """加载保存的Cookie"""
        try:
            with open('cookies.json', 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}  # 返回空字典如果文件不存在或解析失败

    def open_url(self, url):
        """打开商品详情页面"""
        QDesktopServices.openUrl(QUrl(url))  # 使用QUrl打开链接，避免浏览器崩溃

    def save_min_price_products(self):
        """保存历史最低价商品记录到文件"""
        try:
            with open('min_price_history.json', 'w', encoding='utf-8') as f:
                json.dump(self.min_price_products, f, ensure_ascii=False)
            logger.info("历史最低价商品记录已保存")
        except Exception as e:
            logger.error(f"保存历史最低价商品记录失败: {str(e)}")

    def load_min_price_products(self):
        """从本地文件加载历史最低价商品记录"""
        try:
            if os.path.exists('min_price_history.json'):
                with open('min_price_history.json', 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 确保每个记录都有id字段
                self.min_price_products = {}
                for name, product in data.items():
                    if 'id' not in product:
                        # 如果没有id，用name代替
                        product['id'] = name.replace(' ', '_')
                    self.min_price_products[name] = product
                
                logger.info(f"已加载 {len(self.min_price_products)} 件历史最低价商品记录")
                # 更新右侧边栏
                self.update_sidebar()
        except Exception as e:
            logger.error(f"加载历史最低价商品记录失败: {str(e)}")
            self.min_price_products = {}

    def clear_min_price_history(self):
        """清除历史最低价商品记录"""
        reply = QMessageBox.question(self, "确认清除", "确定要清除所有历史最低价商品记录吗？",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.min_price_products = {}
            self.save_min_price_products()
            self.update_sidebar()
            self.update_status("历史最低价商品记录已清除")
    
    def clear_product_history(self):
        """清除左侧商品历史记录"""
        reply = QMessageBox.question(self, "确认清除", "确定要清除左侧所有商品记录吗？",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            # 安全地清除商品并释放资源
            while self.grid.count():
                item = self.grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
                    
            self.product_cache.clear()
            self.total_products_count = 0
            self.update_status("左侧商品记录已清除")
            self.update_statistics()

    def init_ui(self):
        """初始化界面（优化版）"""
        self.setWindowTitle('B站市集实时监控')
        self.setMinimumSize(1280, 720)
        self.setStyleSheet("""
            QMainWindow {
                background: #F5F7FA;
            }
            QLabel {
                color: #606266;
            }
            QPushButton {
                background: #409EFF;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                min-width: 80px;
            }
            QPushButton:hover {
                background: #66B1FF;
            }
            QPushButton:pressed {
                background: #3A8EE6;
            }
            QPushButton:disabled {
                background: #C0C4CC;
            }
            QLineEdit {
                padding: 8px;
                border: 1px solid #DCDFE6;
                border-radius: 4px;
                font: 14px;
            }
            QLineEdit:focus {
                border: 1px solid #409EFF;
            }
            QScrollArea {
                border: none;
            }
            QFrame {
                border-radius: 8px;
            }
            QSpinBox {
                padding: 5px;
                border: 1px solid #DCDFE6;
                border-radius: 4px;
            }
            QTableWidget {
                border: none;
                gridline-color: #EBEEF5;
            }
            QTableWidget::item {
                padding: 8px;
            }
            QHeaderView::section {
                background: #F5F7FA;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #EBEEF5;
            }
        """)

        # 主容器
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(12)

        # 顶部控制栏
        control_bar = QWidget()
        control_layout = QHBoxLayout(control_bar)
        control_layout.setContentsMargins(0, 0, 0, 0)
        
        # Cookie输入
        self.cookie_input = QLineEdit()
        self.cookie_input.setPlaceholderText("请输入B站Cookie（格式：name1=value1; name2=value2）")
        self.cookie_input.setMinimumWidth(400)
        self.cookie_input.setText(self.cookies.get("cookie", ""))
        
        # 控制按钮
        self.save_cookie_btn = QPushButton("保存Cookie")
        self.save_cookie_btn.clicked.connect(self.save_cookie)
        self.refresh_btn = QPushButton("刷新(3页)")
        self.refresh_btn.clicked.connect(self.refresh_data)
        self.load_more_btn = QPushButton("加载更多")
        self.load_more_btn.clicked.connect(self.load_more_data)
        self.load_more_btn.setStyleSheet("""
            background: #67C23A;
            color: white;
        """)
        
        # 添加暂停按钮
        self.pause_btn = QPushButton("暂停刷新")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.pause_btn.setStyleSheet("""
            background: #F56C6C;
            color: white;
        """)
        
        # 价格提醒按钮
        self.price_alert_btn = QPushButton("价格提醒")
        self.price_alert_btn.clicked.connect(self.toggle_price_alert)
        self.price_alert_btn.setStyleSheet("""
            background: #909399;
            color: white;
        """)
        
        # 如果价格提醒已开启，更新按钮状态
        if self.price_alert_enabled:
            self.price_alert_btn.setText(f"价格提醒: {self.price_alert_threshold}元")
            self.price_alert_btn.setStyleSheet("""
                background: #409EFF;
                color: white;
            """)
        
        self.clear_history_btn = QPushButton("清除最低价")
        self.clear_history_btn.setStyleSheet("""
            background: #E6A23C;
            color: white;
        """)
        self.clear_history_btn.clicked.connect(self.clear_min_price_history)
        
        self.clear_products_btn = QPushButton("清除商品")
        self.clear_products_btn.setStyleSheet("""
            background: #E6A23C;
            color: white;
        """)
        self.clear_products_btn.clicked.connect(self.clear_product_history)
        
        control_layout.addWidget(QLabel("Cookie:"))
        control_layout.addWidget(self.cookie_input)
        control_layout.addWidget(self.save_cookie_btn)
        control_layout.addStretch()
        control_layout.addWidget(self.refresh_btn)
        control_layout.addWidget(self.load_more_btn)
        control_layout.addWidget(self.pause_btn)
        control_layout.addWidget(self.price_alert_btn)
        control_layout.addWidget(self.clear_history_btn)
        control_layout.addWidget(self.clear_products_btn)

        # 第二行控制栏 - 设置和倒计时
        settings_bar = QWidget()
        settings_layout = QHBoxLayout(settings_bar)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        
        # 刷新间隔设置
        settings_layout.addWidget(QLabel("刷新间隔:"))
        self.refresh_spinner = QSpinBox()
        self.refresh_spinner.setRange(5, 300)
        self.refresh_spinner.setValue(self.refresh_interval)
        self.refresh_spinner.setSuffix(' 秒')
        self.refresh_spinner.valueChanged.connect(self.update_refresh_interval)
        settings_layout.addWidget(self.refresh_spinner)
        
        # API冷却时间设置
        settings_layout.addWidget(QLabel("API冷却:"))
        self.cooldown_spinner = QSpinBox()
        self.cooldown_spinner.setRange(1, 10)
        self.cooldown_spinner.setValue(self.api_cooldown // 1000)  # 毫秒转秒
        self.cooldown_spinner.setSuffix(' 秒')
        self.cooldown_spinner.valueChanged.connect(self.update_cooldown)
        settings_layout.addWidget(self.cooldown_spinner)
        
        # 列数设置
        settings_layout.addWidget(QLabel("每行列数:"))
        self.columns_spinner = QSpinBox()
        self.columns_spinner.setRange(1, 12)
        self.columns_spinner.setValue(self.columns_count)
        self.columns_spinner.valueChanged.connect(self.update_columns)
        settings_layout.addWidget(self.columns_spinner)
        
        # 添加倒计时显示
        settings_layout.addStretch()
        self.countdown_label = QLabel(f"下次刷新: {self.refresh_interval} 秒")
        self.countdown_label.setStyleSheet("""
            padding: 5px 10px;
            background: #F2F6FC;
            border-radius: 4px;
            font-weight: bold;
        """)
        settings_layout.addWidget(self.countdown_label)
        
        # 初始化倒计时
        self.remaining_time = self.refresh_interval

        # 主体内容区
        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setSpacing(12)
        
        # 左侧商品列表
        left_panel = QFrame()
        left_panel.setFrameShape(QFrame.StyledPanel)
        left_panel.setStyleSheet("""
            background: white;
            border-radius: 8px;
            border: 1px solid #EBEEF5;
        """)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(12, 12, 12, 12)
        
        # 搜索框和标题
        left_header = QHBoxLayout()
        left_title = QLabel("商品监控列表")
        left_title.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        left_title.setStyleSheet("color: #303133;")
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 搜索商品名称...")
        self.search_input.textChanged.connect(self.filter_products)
        self.search_input.setMinimumWidth(200)
        
        left_header.addWidget(left_title)
        left_header.addStretch()
        left_header.addWidget(self.search_input)
        left_layout.addLayout(left_header)
        
        # 统计信息区域
        self.stats_frame = QFrame()
        self.stats_frame.setStyleSheet("""
            background: #F2F6FC;
            border-radius: 4px;
            padding: 8px;
            margin-top: 8px;
            margin-bottom: 8px;
        """)
        stats_layout = QHBoxLayout(self.stats_frame)
        stats_layout.setContentsMargins(8, 8, 8, 8)
        
        self.total_label = QLabel("总商品数: 0")
        self.total_label.setStyleSheet("font: bold 12px;")
        
        self.refresh_count_label = QLabel("本次刷新: 0 件")
        self.refresh_count_label.setStyleSheet("font: 12px;")
        
        self.time_label = QLabel("上次刷新: --")
        self.time_label.setStyleSheet("font: 12px;")
        
        stats_layout.addWidget(self.total_label)
        stats_layout.addWidget(self.refresh_count_label)
        stats_layout.addWidget(self.time_label)
        stats_layout.addStretch()
        
        left_layout.addWidget(self.stats_frame)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent;")
        self.container = QWidget()
        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(8)  # 减小间距
        self.grid.setContentsMargins(4, 4, 4, 4)  # 减小边距
        self.scroll.setWidget(self.container)
        left_layout.addWidget(self.scroll)

        # 右侧边栏
        right_panel = QFrame()
        right_panel.setFrameShape(QFrame.StyledPanel)
        right_panel.setStyleSheet("""
            background: white;
            border-radius: 8px;
            border: 1px solid #EBEEF5;
        """)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 12, 12, 12)
        
        # 右侧标题和搜索框
        right_header = QHBoxLayout()
        right_title = QLabel("历史最低价商品")
        right_title.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        right_title.setStyleSheet("color: #303133;")
        
        self.sidebar_search_input = QLineEdit()
        self.sidebar_search_input.setPlaceholderText("🔍 搜索低价商品...")
        self.sidebar_search_input.textChanged.connect(self.filter_sidebar_products)
        self.sidebar_search_input.setMinimumWidth(150)
        
        right_header.addWidget(right_title)
        right_header.addStretch()
        right_header.addWidget(self.sidebar_search_input)
        right_layout.addLayout(right_header)

        # 右侧使用网格布局，与左侧一致
        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setStyleSheet("background: transparent;")
        self.sidebar_container = QWidget()
        self.sidebar_grid = QGridLayout(self.sidebar_container)
        self.sidebar_grid.setSpacing(8)  # 减小间距
        self.sidebar_grid.setContentsMargins(4, 4, 4, 4)  # 减小边距
        self.sidebar_scroll.setWidget(self.sidebar_container)
        right_layout.addWidget(self.sidebar_scroll)

        # 设置分割比例
        content_layout.addWidget(left_panel, 7)  # 左侧占70%
        content_layout.addWidget(right_panel, 3)  # 右侧占30%

        # 状态栏
        self.status_bar = QLabel()
        self.status_bar.setStyleSheet("""
            color: #909399;
            font: 12px;
            padding: 8px;
            border-top: 1px solid #EBEEF5;
        """)

        main_layout.addWidget(control_bar)
        main_layout.addWidget(settings_bar)
        main_layout.addWidget(content_widget)
        main_layout.addWidget(self.status_bar)

        # 定时器
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_data)
        self.timer.start(self.refresh_interval * 1000)
        
        # 初始化布局
        self.refresh_layout_with_recent_first()
        self.update_status("准备就绪")
        
    def refresh_layout(self):
        """刷新界面布局，显示已缓存的商品"""
        try:
            # 安全地清除网格布局中的所有控件，但不删除控件本身
            while self.grid.count():
                item = self.grid.takeAt(0)
                if item and item.widget():
                    item.widget().setParent(None)
                    
            # 按照添加时间排序（保持最新的商品在前面）
            row = 0
            col = 0
            # 使用列表而不是字典的values()方法，避免迭代过程中的顺序问题
            widgets = list(self.product_cache.values())
            # 从后向前迭代，保证最新添加的在前面
            for widget in reversed(widgets):
                self.grid.addWidget(widget, row, col)
                col += 1
                if col >= 4:  # 每行4个商品，增加一行的商品数量
                    col = 0
                    row += 1
                    
            # 更新统计信息
            self.total_products_count = len(self.product_cache)
            self.update_statistics()
        except Exception as e:
            logger.error(f"刷新布局时出错: {str(e)}")
            self.update_status(f"刷新布局时出错: {str(e)}")
    
    def load_more_data(self):
        """加载更多商品数据"""
        self.load_more_btn.setEnabled(False)
        self.update_status("正在加载更多商品...")
        # 这里不重置nextId，继续使用上次的值
        self.worker_thread.auto_load_more = False  # 手动加载时不自动继续加载
        self.worker_thread.start()
        
    def update_statistics(self):
        """更新统计信息"""
        self.total_label.setText(f"总商品数: {self.total_products_count}")
        self.refresh_count_label.setText(f"本次刷新: {self.last_refresh_count} 件")
        self.time_label.setText(f"上次刷新: {time.strftime('%H:%M:%S')}")

    def update_products(self, products):
        """优化性能的商品更新"""
        if not products:
            self.refresh_btn.setEnabled(True)
            self.load_more_btn.setEnabled(True)
            self.update_status("未获取到商品数据")
            return
        
        try:
            # 检查价格提醒
            self.check_price_alerts(products)
            
            # 记录本次刷新的商品数量
            self.last_refresh_count = len(products)
            logger.info(f"本次获取到 {self.last_refresh_count} 件商品")
            
            # 保存上次刷新的标记，准备标记新的一批
            old_refresh_ids = self.last_refresh_ids.copy()
            # 获取当前刷新商品的ID集合，作为最近刷新的标记
            self.last_refresh_ids = {product['id'] for product in products}
            
            # 首先取消之前刷新商品的标记
            for pid in old_refresh_ids:
                if pid in self.product_cache:
                    # 只更新样式为非新刷新，而不是重新创建卡片
                    card = self.product_cache[pid]
                    card.setStyleSheet("""
                        QFrame {
                            background: white;
                            border-radius: 8px;
                            border: 1px solid #EBEEF5;
                        }
                        QFrame:hover {
                            border: 1px solid #409EFF;
                            background: #F5F7FA;
                        }
                    """)
                    # 查找并移除"刚刷新"标签
                    for i in range(card.layout().count()):
                        item = card.layout().itemAt(i)
                        if item and item.layout():
                            for j in range(item.layout().count()):
                                widget = item.layout().itemAt(j).widget()
                                if isinstance(widget, QLabel) and widget.text() == "新":
                                    widget.hide()
                                    widget.deleteLater()
            
            # 分别记录新商品和更新商品的数量
            new_products_count = 0
            updated_products_count = 0
            
            # 遍历刷新到的所有商品（不管是否存在）
            for product in products:
                if product['id'] not in self.product_cache:
                    # 新商品，创建卡片并添加到缓存
                    self.product_cache[product['id']] = self.add_product_card(product, is_new=True)
                    new_products_count += 1
                    logger.info(f"添加新商品: {product['id']} - {product['name']} - ¥{product['price']}")
                else:
                    # 已存在的商品，更新为新刷新状态
                    old_card = self.product_cache[product['id']]
                    # 更新样式为新刷新
                    old_card.setStyleSheet("""
                        QFrame {
                            background: #EDF8FF;
                            border-radius: 8px;
                            border: 1px solid #409EFF;
                        }
                        QFrame:hover {
                            border: 2px solid #409EFF;
                            background: #F0F9FF;
                        }
                    """)
                    # 添加"刚刷新"标签
                    for i in range(old_card.layout().count()):
                        item = old_card.layout().itemAt(i)
                        if item and item.layout() and "price_layout" in item.layout().objectName():
                            # 找到价格布局，添加"刚刷新"标签
                            price_layout = item.layout()
                            new_label = QLabel("新")
                            new_label.setStyleSheet("""
                                background: #67C23A;
                                color: white;
                                font: bold 8px;
                                padding: 1px 2px;
                                border-radius: 3px;
                            """)
                            price_layout.insertWidget(0, new_label)
                            break
                    updated_products_count += 1
                    logger.info(f"更新商品标记: {product['id']} - {product['name']} - ¥{product['price']}")
            
            # 更新总商品数
            self.total_products_count = len(self.product_cache)
            logger.info(f"商品缓存总数: {self.total_products_count}, 新增: {new_products_count}, 更新: {updated_products_count}")
            
            # 重新布局所有商品，把最近刷新的放在最前面
            self.refresh_layout_with_recent_first()
            
            # 保存最低价商品记录
            if self.min_price_products:
                self.save_min_price_products()
            
            # 保存商品缓存
            self.save_product_cache()
                
            # 更新右侧边栏
            self.update_sidebar()
            self.refresh_btn.setEnabled(True)
            self.load_more_btn.setEnabled(True)
            
            # 更新统计信息
            self.update_statistics()
            
            status_msg = f"已获取 {len(products)} 件商品，新增 {new_products_count} 件，总计 {self.total_products_count} 件"
            self.update_status(status_msg)
        except Exception as e:
            logger.error(f"更新商品时出错: {str(e)}")
            self.update_status(f"更新商品时出错: {str(e)}")
            self.refresh_btn.setEnabled(True)
            self.load_more_btn.setEnabled(True)

    def refresh_layout_with_recent_first(self):
        """刷新界面布局，把最近刷新的商品放在最前面"""
        try:
            # 安全地清除网格布局中的所有控件，但不删除控件本身
            while self.grid.count():
                item = self.grid.takeAt(0)
                if item and item.widget():
                    item.widget().setParent(None)
                    
            # 首先添加最近刷新的商品
            recent_widgets = []
            other_widgets = []
            
            # 分组商品卡片
            for product_id, widget in self.product_cache.items():
                if product_id in self.last_refresh_ids:
                    recent_widgets.append(widget)
                else:
                    other_widgets.append(widget)
            
            logger.info(f"刷新布局: 最近刷新 {len(recent_widgets)} 件, 其他商品 {len(other_widgets)} 件")
            
            # 使用设置的列数
            row = 0
            col = 0
            
            # 先放置最近刷新的商品
            for widget in recent_widgets:
                self.grid.addWidget(widget, row, col)
                col += 1
                if col >= self.columns_count:  # 使用设置的列数
                    col = 0
                    row += 1
            
            # 再放置其他商品
            for widget in other_widgets:
                self.grid.addWidget(widget, row, col)
                col += 1
                if col >= self.columns_count:  # 使用设置的列数
                    col = 0
                    row += 1
                    
            # 更新统计信息
            self.total_products_count = len(self.product_cache)
            self.update_statistics()
            logger.info(f"完成布局刷新，总共 {row*self.columns_count + col} 个位置，{self.total_products_count} 件商品")
        except Exception as e:
            logger.error(f"刷新布局时出错: {str(e)}")
            self.update_status(f"刷新布局时出错: {str(e)}")

    def add_product_card(self, product, is_new=False):
        """创建商品卡片"""
        card = QFrame()
        card.setObjectName(f"product_{product['id']}")
        
        # 根据是否是最近刷新的商品设置不同的样式
        if is_new:
            card.setStyleSheet("""
                QFrame {
                    background: #EDF8FF;
                    border-radius: 8px;
                    border: 1px solid #409EFF;
                }
                QFrame:hover {
                    border: 2px solid #409EFF;
                    background: #F0F9FF;
                }
            """)
        else:
            card.setStyleSheet("""
                QFrame {
                    background: white;
                    border-radius: 8px;
                    border: 1px solid #EBEEF5;
                }
                QFrame:hover {
                    border: 1px solid #409EFF;
                    background: #F5F7FA;
                }
            """)
            
        # 进一步缩小卡片尺寸
        card.setFixedSize(self.card_width, self.card_height)
        card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(3)

        img_label = QLabel()
        img_label.setObjectName("image")
        img_width = self.card_width - 10
        img_height = int(self.card_height * 0.55)  # 图片高度占卡片的55%
        img_label.setFixedSize(img_width, img_height)
        img_label.setAlignment(Qt.AlignCenter)
        img_label.setStyleSheet("background: #F5F7FA; border-radius: 4px;")
        
        # 如果product有image属性且不为空，则加载图片
        if 'image' in product and product['image']:
            self.thread_pool.start(ImageLoader(
                product['image'],
                partial(self.load_image, img_label)
            ))

        name_label = QLabel(product['name'])
        name_label.setObjectName("name")
        name_label.setWordWrap(True)
        name_label.setStyleSheet("""
            font: 10px 'Microsoft YaHei';
            color: #303133;
            max-height: 30px;
        """)
        name_label.setToolTip(product['name'])  # 添加工具提示，鼠标悬停时显示完整名称
        name_label.setMaximumHeight(30)

        price_layout = QHBoxLayout()
        price_layout.setObjectName("price_layout")  # 添加对象名，便于后续查找
        price_layout.setSpacing(2)
        price_label = QLabel(f"¥{product['price']:.2f}")
        price_label.setObjectName("price")
        
        # 设置价格标签样式
        if is_new:
            price_label.setStyleSheet("""
                font: bold 11px;
                color: #E6A23C;
                background: #FDF6EC;
                padding: 1px 2px;
                border-radius: 3px;
            """)
        else:
            price_label.setStyleSheet("""
                font: bold 11px;
                color: #E6A23C;
            """)
        
        view_btn = QPushButton("查看")
        view_btn.setFixedSize(36, 20)  # 减小按钮尺寸
        view_btn.setStyleSheet("""
            background: #409EFF;
            color: white;
            border-radius: 3px;
            font: 10px;
            padding: 1px;
        """)
        
        # 使用lambda捕获具体的product信息，避免引用问题
        view_url = product['detail_url'] if 'detail_url' in product else f"https://mall.bilibili.com/neul-next/index.html?itemsId={product['id']}"
        view_btn.clicked.connect(lambda: self.open_url(view_url))
        
        # 如果是最近刷新的商品，添加一个"NEW"标签
        if is_new:
            new_label = QLabel("新")
            new_label.setStyleSheet("""
                background: #67C23A;
                color: white;
                font: bold 8px;
                padding: 1px 2px;
                border-radius: 3px;
            """)
            price_layout.addWidget(new_label)
            
        price_layout.addWidget(price_label)
        price_layout.addStretch()
        price_layout.addWidget(view_btn)

        layout.addWidget(img_label)
        layout.addWidget(name_label)
        layout.addLayout(price_layout)

        # 使用lambda捕获具体的URL，避免引用问题
        card.mousePressEvent = lambda e: self.open_url(view_url)
        
        return card

    def update_sidebar(self):
        """更新右侧边栏显示的历史最低价商品"""
        try:
            # 安全地清除网格布局中的所有控件
            while self.sidebar_grid.count():
                item = self.sidebar_grid.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()

            # 按价格排序历史最低价商品（从低到高）
            sorted_products = sorted(self.min_price_products.values(), key=lambda x: x['price'])
                    
            # 以网格形式显示历史最低价商品
            row = 0
            col = 0
            for product in sorted_products:
                # 确保产品有id字段
                min_price_product = {
                    'id': product.get('name', '').replace(' ', '_'),  # 使用name作为id
                    'name': product.get('name', '未知商品'),
                    'price': product.get('price', 0),
                    'image': product.get('image', ''),
                    'detail_url': product.get('url', '')
                }
                # 使用同样的卡片样式，但不标记为新商品
                card = self.add_product_card(min_price_product, is_new=False)
                self.sidebar_grid.addWidget(card, row, col)
                col += 1
                if col >= self.sidebar_columns_count:  # 使用设置的列数
                    col = 0
                    row += 1
                
            self.update_status(f"侧边栏已更新 {len(sorted_products)} 件最低价商品")
        except Exception as e:
            logger.error(f"更新侧边栏时出错: {str(e)}")
            self.update_status(f"更新侧边栏时出错: {str(e)}")

    def save_product_cache(self):
        """保存商品缓存到本地文件"""
        try:
            # 只保存必要的信息，不保存UI组件
            cache_data = {}
            for pid, widget in self.product_cache.items():
                name_label = widget.findChild(QLabel, "name")
                price_label = widget.findChild(QLabel, "price")
                
                if name_label and price_label:
                    name = name_label.text()
                    price = price_label.text().replace("¥", "")
                    
                    try:
                        price_float = float(price)
                    except ValueError:
                        price_float = 0.0
                        
                    cache_data[pid] = {
                        'id': pid,
                        'name': name,
                        'price': price_float,
                        'image': '',  # 图片URL可能会变化，暂不保存
                        'detail_url': f"https://mall.bilibili.com/neul-next/index.html?itemsId={pid}"
                    }
            
            with open('product_cache.json', 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False)
            logger.info(f"已保存 {len(cache_data)} 件商品缓存")
        except Exception as e:
            logger.error(f"保存商品缓存失败: {str(e)}")

    def load_product_cache(self):
        """从本地文件加载商品缓存"""
        try:
            if os.path.exists('product_cache.json'):
                with open('product_cache.json', 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                
                # 创建商品卡片并添加到缓存
                for pid, data in cache_data.items():
                    self.product_cache[pid] = self.add_product_card(data)
                
                self.total_products_count = len(self.product_cache)
                logger.info(f"已加载 {self.total_products_count} 件商品缓存")
                
                # 输出加载的商品详情，便于调试
                for pid, data in list(cache_data.items())[:5]:  # 只输出前5个，避免日志过长
                    logger.info(f"加载商品: {pid} - {data.get('name')} - ¥{data.get('price')}")
                if len(cache_data) > 5:
                    logger.info(f"... 还有 {len(cache_data) - 5} 件商品")
        except Exception as e:
            logger.error(f"加载商品缓存失败: {str(e)}")
            self.product_cache = {}
            self.total_products_count = 0
            
    def closeEvent(self, event):
        """程序关闭时保存数据"""
        self.save_min_price_products()
        self.save_product_cache()
        self.save_settings()
        super().closeEvent(event)

    def auto_load_more(self):
        """自动加载更多商品，由信号触发"""
        QTimer.singleShot(self.api_cooldown, lambda: self.worker_thread.start())  # 使用设置的冷却时间

    def update_countdown(self):
        """更新倒计时显示"""
        if not self.is_paused and self.timer.isActive():
            self.remaining_time -= 1
            if self.remaining_time < 0:
                self.remaining_time = self.refresh_interval
            
            # 更新倒计时显示
            self.countdown_label.setText(f"下次刷新: {self.remaining_time} 秒")
            
            # 如果剩余时间小于5秒，改变颜色提示
            if self.remaining_time <= 5:
                self.countdown_label.setStyleSheet("color: #E6A23C; font-weight: bold;")
            else:
                self.countdown_label.setStyleSheet("color: #606266;")
        
    def toggle_pause(self):
        """暂停或继续自动刷新"""
        self.is_paused = not self.is_paused
        
        if self.is_paused:
            # 暂停定时器
            self.timer.stop()
            self.pause_btn.setText("继续刷新")
            self.pause_btn.setStyleSheet("""
                background: #67C23A;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                min-width: 80px;
            """)
            self.countdown_label.setText("已暂停自动刷新")
            self.countdown_label.setStyleSheet("color: #F56C6C; font-weight: bold;")
            self.update_status("已暂停自动刷新")
        else:
            # 继续定时器
            self.timer.start(self.refresh_interval * 1000)
            self.pause_btn.setText("暂停刷新")
            self.pause_btn.setStyleSheet("""
                background: #F56C6C;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                min-width: 80px;
            """)
            self.remaining_time = self.refresh_interval
            self.update_status("已恢复自动刷新")
            
    def toggle_price_alert(self):
        """开启或关闭价格提醒"""
        self.price_alert_enabled = not self.price_alert_enabled
        
        if self.price_alert_enabled:
            # 打开价格提醒设置对话框
            threshold, ok = QInputDialog.getDouble(
                self, "设置价格提醒", "请输入价格提醒阈值（低于此价格时提醒）:",
                self.price_alert_threshold, 0, 10000, 2
            )
            
            if ok:
                self.price_alert_threshold = threshold
                self.price_alert_btn.setText(f"价格提醒: {threshold}元")
                self.price_alert_btn.setStyleSheet("""
                    background: #409EFF;
                    color: white;
                """)
                self.update_status(f"已开启价格提醒，低于 {threshold} 元的商品将会提醒")
            else:
                # 用户取消，关闭提醒
                self.price_alert_enabled = False
                self.price_alert_btn.setText("价格提醒")
                self.price_alert_btn.setStyleSheet("""
                    background: #909399;
                    color: white;
                """)
        else:
            # 关闭价格提醒
            self.price_alert_btn.setText("价格提醒")
            self.price_alert_btn.setStyleSheet("""
                background: #909399;
                color: white;
            """)
            self.update_status("已关闭价格提醒")
            
    def check_price_alerts(self, products):
        """检查商品价格是否低于提醒阈值"""
        if not self.price_alert_enabled or self.price_alert_threshold <= 0:
            return
            
        alert_products = []
        for product in products:
            if product['price'] <= self.price_alert_threshold:
                alert_products.append(product)
                
        if alert_products:
            # 发出系统通知
            self.show_price_alert(alert_products)
            
    def show_price_alert(self, products):
        """显示价格提醒"""
        if not products:
            return
            
        # 构建提醒消息
        message = f"发现{len(products)}件低价商品:\n\n"
        for i, product in enumerate(products[:5]):  # 最多显示5个
            message += f"{i+1}. {product['name']} - ¥{product['price']:.2f}\n"
            
        if len(products) > 5:
            message += f"...以及其他{len(products)-5}件商品"
            
        # 显示提醒对话框
        alert = QMessageBox(self)
        alert.setWindowTitle("价格提醒")
        alert.setText(message)
        alert.setIcon(QMessageBox.Information)
        
        # 添加查看全部按钮
        view_btn = alert.addButton("查看全部", QMessageBox.ActionRole)
        alert.addButton("关闭", QMessageBox.RejectRole)
        
        # 高亮显示
        self.activateWindow()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        
        alert.exec_()
        
        # 如果点击了查看全部
        if alert.clickedButton() == view_btn:
            self.show_all_alert_products(products)
            
    def show_all_alert_products(self, products):
        """显示所有提醒商品的详细信息"""
        # 创建对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("低价商品列表")
        dialog.setMinimumSize(600, 400)
        
        # 创建布局
        layout = QVBoxLayout(dialog)
        
        # 创建表格
        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["商品名称", "价格", "时间", "操作", ""])
        table.horizontalHeader().setSectionResizeMode(0, QTableWidget.Stretch)
        table.setRowCount(len(products))
        
        # 填充表格
        for row, product in enumerate(products):
            # 商品名称
            name_item = QTableWidgetItem(product['name'])
            name_item.setToolTip(product['name'])
            table.setItem(row, 0, name_item)
            
            # 价格
            price_item = QTableWidgetItem(f"¥{product['price']:.2f}")
            price_item.setTextAlignment(Qt.AlignCenter)
            price_item.setForeground(QColor("#E6A23C"))
            table.setItem(row, 1, price_item)
            
            # 时间
            time_item = QTableWidgetItem(time.strftime("%H:%M:%S"))
            time_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(row, 2, time_item)
            
            # 操作按钮
            view_btn = QPushButton("查看商品")
            view_btn.clicked.connect(lambda _, url=product['detail_url']: self.open_url(url))
            view_btn.setStyleSheet("""
                background: #409EFF;
                color: white;
                border-radius: 4px;
                padding: 4px;
            """)
            
            # 将按钮添加到表格中
            table.setCellWidget(row, 3, view_btn)
            
            # 复制按钮
            copy_btn = QPushButton("复制名称")
            copy_btn.clicked.connect(lambda _, name=product['name']: self.copy_to_clipboard(name))
            copy_btn.setStyleSheet("""
                background: #67C23A;
                color: white;
                border-radius: 4px;
                padding: 4px;
            """)
            
            table.setCellWidget(row, 4, copy_btn)
        
        layout.addWidget(table)
        
        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.exec_()
        
    def copy_to_clipboard(self, text):
        """复制文本到剪贴板"""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.update_status(f"已复制到剪贴板: {text[:20]}...")

class WorkerThread(QThread):
    update_signal = pyqtSignal(list)
    error_signal = pyqtSignal(str)
    auto_load_signal = pyqtSignal()  # 添加自动加载更多信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.next_id = None  # 用于保存下一页的ID
        self.is_refresh = False  # 标记是否为刷新操作
        self.auto_load_more = False  # 是否自动加载更多
        self.auto_load_pages = 3  # 自动加载的页数
        self.api_cooldown = 2000  # API请求冷却时间(毫秒)

    def run(self):
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Cookie": self.parent.cookies.get("cookie", "")
            }
            
            if self.is_refresh:
                logger.info(f"执行刷新操作，重置nextId为None")
                self.next_id = None
                self.is_refresh = False
            
            logger.info(f"开始请求商品数据，操作类型: {'刷新' if self.next_id is None else '加载更多'}, nextId: {self.next_id}")
            
            # 发送请求获取商品数据
            request_data = {"sortType": "TIME_DESC", "nextId": self.next_id}
            logger.info(f"请求参数: {request_data}")
            
            response = requests.post(
                "https://mall.bilibili.com/mall-magic-c/internet/c2c/v2/list",
                json=request_data,
                headers=headers,
                timeout=15
            )
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"API响应状态码: {data.get('code')}, 消息: {data.get('message')}")
                
                products = self.process_response(data)
                logger.info(f"成功处理 {len(products)} 件商品数据")
                
                # 保存nextId用于下次加载
                next_id = data.get('data', {}).get('nextId')
                if next_id:
                    old_next_id = self.next_id
                    self.next_id = next_id
                    logger.info(f"更新nextId: {old_next_id} -> {self.next_id}")
                    
                    # 如果设置了自动加载更多，且还有页数需要加载
                    if self.auto_load_more and self.auto_load_pages > 1:
                        self.auto_load_pages -= 1
                        logger.info(f"启动自动加载更多，剩余页数: {self.auto_load_pages}")
                        # 发送信号，触发再次加载
                        self.auto_load_signal.emit()
                else:
                    logger.info("没有更多商品数据了，nextId为空")
                    self.auto_load_more = False
                
                self.update_signal.emit(products)
            else:
                self.error_signal.emit(f"请求失败 [{response.status_code}]")
        except Exception as e:
            self.error_signal.emit(f"网络错误: {str(e)}")
            logger.error(f"请求商品数据出错: {str(e)}")

    def process_response(self, data):
        """优化数据处理性能"""
        try:
            if data.get('code') != 0:
                raise ValueError(data.get('message', '未知错误'))
            
            products = []
            for item in data.get('data', {}).get('data', []):
                try:
                    product_id = str(item.get('c2cItemsId', ''))
                    detail = item.get('detailDtoList', [{}])[0]
                    
                    product = {
                        'id': product_id,
                        'name': detail.get('name', '未知商品').strip(),
                        'price': item.get('price', 0) / 100,
                        'image': f"https:{detail.get('img', '')}",
                        'detail_url': f"https://mall.bilibili.com/neul-next/index.html?itemsId={product_id}"
                    }
                    
                    # 更新最低价记录，确保记录中包含id字段
                    if product['name'] not in self.parent.min_price_products or \
                       product['price'] < self.parent.min_price_products[product['name']]['price']:
                        self.parent.min_price_products[product['name']] = {
                            'id': product_id,  # 添加id字段
                            'name': product['name'],
                            'price': product['price'],
                            'image': product['image'],
                            'url': product['detail_url'],
                            'timestamp': time.time()
                        }
                    
                    products.append(product)
                except Exception as e:
                    logger.error(f"商品数据处理异常: {str(e)}")
            
            return products
        except Exception as e:
            self.error_signal.emit(f"数据处理失败: {str(e)}")
            return []

    def refresh_data(self):
        """设置为刷新模式，并启用自动加载更多"""
        self.is_refresh = True
        self.auto_load_more = True
        self.auto_load_pages = 3  # 自动加载3页

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ProductMonitor()
    window.show()
    sys.exit(app.exec_())
