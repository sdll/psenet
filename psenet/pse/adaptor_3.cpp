#include "pybind11/numpy.h"
#include "pybind11/pybind11.h"
#include "pybind11/stl.h"
#include "pybind11/stl_bind.h"

#include <iostream>
#include <queue>

#include <opencv2/core/core.hpp>
#include <opencv2/highgui/highgui.hpp>
#include <opencv2/imgproc/imgproc.hpp>
#include <opencv2/opencv.hpp>

using namespace std;
using namespace cv;

namespace py = pybind11;

namespace lanms_adaptor {
void get_kernels(const int *data, vector<long int> data_shape,
                 vector<Mat> &kernels) {
  // vector<Mat> kernels;
  for (int i = 0; i < data_shape[0]; ++i) {
    Mat kernel = Mat::zeros(data_shape[1], data_shape[2], CV_8UC1);
    for (int x = 0; x < kernel.rows; ++x) {
      for (int y = 0; y < kernel.cols; ++y) {
        kernel.at<char>(x, y) =
            data[i * data_shape[1] * data_shape[2] + x * data_shape[2] + y];
      }
    }
    kernels.emplace_back(kernel);
  }
  // return kernels;
}

void growing_text_line(vector<Mat> &kernels, vector<vector<int>> &text_line) {
  int th1 = 10;
  // int th1 = 0;
  // Mat text_line = Mat::zeros(kernels[0].size(), CV_32SC1);

  Mat label_mat;
  int label_num =
      connectedComponents(kernels[kernels.size() - 1], label_mat, 4);

  int area[label_num + 1];
  memset(area, 0, sizeof(area));
  for (int x = 0; x < label_mat.rows; ++x) {
    for (int y = 0; y < label_mat.cols; ++y) {
      int label = label_mat.at<int>(x, y);
      if (label == 0) continue;
      area[label] += 1;
    }
  }

  // vector<vector<int>> text_line;
  queue<Point> queue, next_queue;
  for (int x = 0; x < label_mat.rows; ++x) {
    vector<int> row(label_mat.cols);
    for (int y = 0; y < label_mat.cols; ++y) {
      int label = label_mat.at<int>(x, y);
      if (label == 0) {
        // row.emplace_back(0);
        continue;
      }
      if (area[label] < th1) {
        // row.emplace_back(0);
        continue;
      }
      Point point(x, y);
      queue.push(point);
      // text_line.at<int>(x, y) = label;
      // row.emplace_back(label);
      row[y] = label;
    }
    text_line.emplace_back(row);
  }

  // cout << text_line << endl;

  int dx[] = {-1, 1, 0, 0};
  int dy[] = {0, 0, -1, 1};

  for (int kernel_id = kernels.size() - 2; kernel_id >= 0; --kernel_id) {
    while (!queue.empty()) {
      Point point = queue.front();
      queue.pop();
      int x = point.x;
      int y = point.y;
      // int label = text_line.at<int>(x, y);
      int label = text_line[x][y];

      bool is_edge = true;
      for (int d = 0; d < 4; ++d) {
        int tmp_x = x + dx[d];
        int tmp_y = y + dy[d];

        if (tmp_x < 0 || tmp_x >= (int)text_line.size()) continue;
        if (tmp_y < 0 || tmp_y >= (int)text_line[1].size()) continue;
        if (kernels[kernel_id].at<char>(tmp_x, tmp_y) == 0) continue;
        if (text_line[tmp_x][tmp_y] > 0) continue;

        Point point(tmp_x, tmp_y);
        queue.push(point);
        text_line[tmp_x][tmp_y] = label;
        is_edge = false;
      }

      if (is_edge) {
        next_queue.push(point);
      }
    }

    /*
    label_num = connectedComponents(kernels[kernel_id], label_mat, 4);

    int area[label_num + 1];
    memset(area, 0, sizeof(area));
    for (int x = 0; x < label_mat.rows; ++x) {
        for (int y = 0; y < label_mat.cols; ++y) {
            int label = label_mat.at<int>(x, y);
            if (label == 0) continue;
            area[label] += 1;
        }
    }

    for (int x = 0; x < label_mat.rows; ++x) {
        for (int y = 0; y < label_mat.cols; ++y) {
            int label = label_mat.at<int>(x, y);
            if (label == 0) continue;
            if (area[label] < th1) continue;
            if (text_line.at<int>(x, y) > 0) continue;
            text_line.at<int>(x, y) = label + bias;
        }
    }
    bias += label_num;
    */

    /*
    for (int x = 0; x < text_line.rows; ++x) {
        for (int y = 0; y < text_line.cols; ++y) {
            if (text_line.at<int>(x, y) == 0) continue;
            Point point(x, y);
            queue.push(point);
        }
    }
    */

    swap(queue, next_queue);
  }

  // cout << text_line << endl;

  // return text_line;
}

vector<vector<int>> merge_quadrangle_n9(
    py::array_t<int, py::array::c_style | py::array::forcecast> quad_n9) {
  auto buf = quad_n9.request();
  auto data = static_cast<int *>(buf.ptr);
  vector<Mat> kernels;
  get_kernels(data, buf.shape, kernels);

  vector<vector<int>> text_line;
  growing_text_line(kernels, text_line);

  // cout << _text_line << endl;
  // vector<vector<int>> text_line;
  // for (int x = 0; x < _text_line.rows; ++x) {
  //     vector<int> row;
  //     for (int y = 0; y < _text_line.cols; ++y) {
  //         row.emplace_back(_text_line.at<int>(x, y));
  //     }
  //     text_line.emplace_back(row);
  // }

  return text_line;
}
}  // namespace lanms_adaptor

PYBIND11_PLUGIN(adaptor) {
  py::module m("adaptor", "NMS");

  m.def("merge_quadrangle_n9", &lanms_adaptor::merge_quadrangle_n9,
        "merge quadrangels");

  return m.ptr();
}
