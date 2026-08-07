#include <Wire.h>
#include <bme68xLibrary.h>
#include <WiFi.h>
#include <HTTPClient.h>

Bme68x bme;

const char* ssid = "D4C";
const char* password = "aarshandwho";
const char* serverUrl = "http://172.20.10.9:5000/data";

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);

  bme.begin(0x76, Wire);
  if (bme.checkStatus() == BME68X_ERROR) {
    Serial.println("BME680 error!");
    while (1);
  }
  bme.setTPH(BME68X_OS_2X, BME68X_OS_16X, BME68X_OS_1X);
  bme.setHeaterProf(300, 100);
  Serial.println("BME680 initialized.");

  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nConnected! IP: " + WiFi.localIP().toString());
}

void loop() {
  bme68xData data;
  bme.setOpMode(BME68X_FORCED_MODE);
  delayMicroseconds(bme.getMeasDur());

  if (bme.fetchData()) {
    bme.getData(data);
    float pressure_hPa = data.pressure / 100.0;

    Serial.printf("Temp: %.2f C | Hum: %.2f %% | Pres: %.2f hPa | Gas: %.0f ohm\n",
                  data.temperature, data.humidity, pressure_hPa, data.gas_resistance);

    if (WiFi.status() == WL_CONNECTED) {
      HTTPClient http;
      http.begin(serverUrl);
      http.addHeader("Content-Type", "application/json");

      String json = "{\"temperature\":" + String(data.temperature) +
                     ",\"humidity\":" + String(data.humidity) +
                     ",\"pressure\":" + String(pressure_hPa) +
                     ",\"gas_resistance\":" + String(data.gas_resistance) + "}";

      int httpCode = http.POST(json);
      Serial.println("POST status: " + String(httpCode));
      http.end();
    }
  } else {
    Serial.println("Sensor read failed");
  }

  delay(3000);
}