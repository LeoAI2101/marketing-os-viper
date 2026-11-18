import assert from 'node:assert/strict';
import test from 'node:test';
import { compareOpenApi } from '../lib/openapi-compat.mjs';

function document() {
  return {
    openapi: '3.0.3',
    info: { title: 'Hợp đồng đặt dịch vụ', version: '1.0.0' },
    paths: {
      '/bookings': {
        post: {
          parameters: [{ name: 'source', in: 'query', required: false,
            schema: { type: 'string' } }],
          requestBody: { required: true, content: {
            'application/json': { schema: { $ref: '#/components/schemas/CreateBooking' } },
          } },
          responses: {
            '201': { description: 'Đã tạo đơn', content: {
              'application/json': { schema: { $ref: '#/components/schemas/Booking' } },
            } },
          },
        },
      },
    },
    components: { schemas: {
      CreateBooking: {
        type: 'object', required: ['serviceId'],
        properties: {
          serviceId: { type: 'string' },
          note: { type: 'string' },
          channel: { type: 'string', enum: ['app', 'web'] },
        },
      },
      Booking: {
        type: 'object', required: ['id', 'status'],
        properties: {
          id: { type: 'string' },
          status: { type: 'string', enum: ['pending', 'confirmed'] },
          note: { type: 'string' },
        },
      },
    } },
  };
}

function operation(doc) { return doc.paths['/bookings'].post; }
function request(doc) { return doc.components.schemas.CreateBooking; }
function response(doc) { return doc.components.schemas.Booking; }

function checkChange(change, breaks) {
  const oldDoc = document();
  const newDoc = structuredClone(oldDoc);
  change(newDoc);
  const diagnostics = compareOpenApi(oldDoc, newDoc);
  assert.ok(Array.isArray(diagnostics));
  if (breaks) assert.ok(diagnostics.length > 0, 'Phải phát hiện thay đổi phá client cũ');
  else assert.equal(diagnostics.length, 0, 'Thay đổi tương thích phải được chấp nhận');
}

const changes = [
  ['xoá endpoint tạo đơn phải bị chặn', true, (doc) => {
    delete doc.paths['/bookings'];
  }],
  ['thêm endpoint mới vẫn giữ client tạo đơn cũ chạy được', false, (doc) => {
    doc.paths['/health'] = { get: { responses: { '204': { description: 'Sẵn sàng' } } } };
  }],
  ['xoá trường phản hồi kể cả trường tuỳ chọn phải bị chặn', true, (doc) => {
    delete response(doc).properties.note;
  }],
  ['trường phản hồi bắt buộc thành tuỳ chọn phải bị chặn', true, (doc) => {
    response(doc).required = ['id'];
  }],
  ['thêm trường bắt buộc trong yêu cầu làm client cũ thiếu dữ liệu', true, (doc) => {
    request(doc).properties.customerId = { type: 'string' };
    request(doc).required.push('customerId');
  }],
  ['thêm trường tuỳ chọn trong yêu cầu không phá client cũ', false, (doc) => {
    request(doc).properties.customerId = { type: 'string' };
  }],
  ['thu hẹp enum yêu cầu phải phát hiện dữ liệu cũ bị từ chối', true, (doc) => {
    request(doc).properties.channel.enum = ['app'];
  }],
  ['mở rộng enum yêu cầu vẫn nhận toàn bộ dữ liệu cũ', false, (doc) => {
    request(doc).properties.channel.enum.push('partner');
  }],
  ['mở rộng enum phản hồi phải phát hiện trạng thái client chưa biết', true, (doc) => {
    response(doc).properties.status.enum.push('cancelled');
  }],
  ['thu hẹp enum phản hồi vẫn nằm trong tập client hiểu được', false, (doc) => {
    response(doc).properties.status.enum = ['confirmed'];
  }],
  ['tham số truy vấn tuỳ chọn thành bắt buộc phải bị chặn', true, (doc) => {
    operation(doc).parameters[0].required = true;
  }],
  ['xoá trường yêu cầu tuỳ chọn phải bị chặn để giữ dữ liệu client gửi', true, (doc) => {
    delete request(doc).properties.note;
  }],
  ['đổi tên tài liệu và mô tả không làm hợp đồng bị báo đỏ', false, (doc) => {
    doc.info.title = 'Đặt dịch vụ 2driver.com';
    doc.info.version = '2.0.0';
    operation(doc).description = 'Tạo yêu cầu đặt dịch vụ';
    response(doc).properties.id.description = 'Mã đơn';
  }],
  ['bỏ kiểu nội dung phản hồi JSON phải bị chặn', true, (doc) => {
    delete operation(doc).responses['201'].content['application/json'];
  }],
  ['bỏ kiểu nội dung yêu cầu JSON phải bị chặn', true, (doc) => {
    delete operation(doc).requestBody.content['application/json'];
  }],
  ['bỏ mã phản hồi đã công bố phải bị chặn', true, (doc) => {
    delete operation(doc).responses['201'];
  }],
];

for (const [name, breaks, change] of changes) {
  test(name, () => checkChange(change, breaks));
}

test('schema được tham chiếu qua mảng đổi kiểu phải bị phát hiện tại phản hồi', () => {
  const oldDoc = document();
  response(oldDoc).properties.items = {
    type: 'array', items: { $ref: '#/components/schemas/BookingItem' },
  };
  oldDoc.components.schemas.BookingItem = {
    type: 'object', required: ['amount'], properties: { amount: { type: 'number' } },
  };
  const newDoc = structuredClone(oldDoc);
  newDoc.components.schemas.BookingItem.properties.amount.type = 'string';
  assert.ok(compareOpenApi(oldDoc, newDoc).length > 0);
});

test('allOf ghép schema nền và thêm trường phản hồi vẫn giữ đủ trường cũ', () => {
  const oldDoc = document();
  const newDoc = structuredClone(oldDoc);
  newDoc.components.schemas.BookingBase = structuredClone(response(oldDoc));
  newDoc.components.schemas.Booking = { allOf: [
    { $ref: '#/components/schemas/BookingBase' },
    { type: 'object', properties: { displayCode: { type: 'string' } } },
  ] };
  assert.equal(compareOpenApi(oldDoc, newDoc).length, 0);
});

test('allOf không được che việc mất trường bắt buộc trong schema nền', () => {
  const oldDoc = document();
  oldDoc.components.schemas.BookingBase = structuredClone(response(oldDoc));
  oldDoc.components.schemas.Booking = {
    allOf: [{ $ref: '#/components/schemas/BookingBase' }],
  };
  const newDoc = structuredClone(oldDoc);
  delete newDoc.components.schemas.BookingBase.properties.id;
  newDoc.components.schemas.BookingBase.required = ['status'];
  assert.ok(compareOpenApi(oldDoc, newDoc).length > 0);
});

for (const direction of ['request', 'response']) {
  test(`map $ref: đổi schema ${direction} phải bị phát hiện`, () => {
    const oldDoc = document();
    const schema = direction === 'request' ? request(oldDoc) : response(oldDoc);
    schema.properties.extra = { type: 'object', additionalProperties: { $ref: '#/components/schemas/Value' } };
    oldDoc.components.schemas.Value = { type: 'string', enum: ['a', 'b'] };
    const next = structuredClone(oldDoc);
    next.components.schemas.Value = direction === 'request' ? { type: 'string', enum: ['a'] } : { type: 'number' };
    assert.ok(compareOpenApi(oldDoc, next).length > 0);
  });
}
test('tham số operation ghi đè path phải được kiểm', () => {
  const oldDoc = document();
  oldDoc.paths['/bookings'].parameters = structuredClone(operation(oldDoc).parameters);
  const next = structuredClone(oldDoc);
  operation(next).parameters[0].required = true;
  assert.ok(compareOpenApi(oldDoc, next).length > 0);
});
test('allOf là giao minimum, không phải nhánh cuối ghi đè', () => {
  const oldDoc = document(); request(oldDoc).properties.amount = { type: 'number', minimum: 0 };
  const next = structuredClone(oldDoc);
  request(next).properties.amount = { allOf: [{ type: 'number', minimum: 10 }, { type: 'number', minimum: 0 }] };
  assert.ok(compareOpenApi(oldDoc, next).length > 0);
});
test('đổi server ở path phải được rà', () => {
  const oldDoc = document(); oldDoc.paths['/bookings'].servers = [{ url: 'https://old.invalid' }];
  const next = structuredClone(oldDoc); next.paths['/bookings'].servers[0].url = 'https://new.invalid';
  assert.ok(compareOpenApi(oldDoc, next).length > 0);
});
